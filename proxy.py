"""
ThaiLLM Proxy for PicoClaw / ZeroClaw
---------------------------
Features:
- Injects 'apikey' header (replaces Authorization: Bearer)
- Strips <think>...</think> reasoning blocks
- Client-side rate limiting (respects ThaiLLM 5 req/s, 200 req/min)
- Handles 429 upstream with auto-retry

Install:
    pip install fastapi uvicorn httpx

Run:
    python proxy.py
"""

import asyncio
import json
import re
import time
from collections import deque

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import uvicorn

from config import (
    THAILLM_BASE_URL,
    THAILLM_API_KEY,
    PROXY_HOST,
    PROXY_PORT,
    STRIP_THINK,
    MAX_PER_SECOND,
    MAX_PER_MINUTE,
    MAX_RETRY_ON_429,
)

app = FastAPI(title="ThaiLLM Proxy")
THINK_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


# ---------- Rate Limiter ----------
class SlidingWindowLimiter:
    """Sliding window rate limiter: enforces both per-second and per-minute."""

    def __init__(self, per_second: int, per_minute: int):
        self.per_second = per_second
        self.per_minute = per_minute
        self.sec_log: deque = deque()
        self.min_log: deque = deque()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """Block until a slot is available, then register the request."""
        while True:
            async with self.lock:
                now = time.monotonic()
                while self.sec_log and now - self.sec_log[0] >= 1.0:
                    self.sec_log.popleft()
                while self.min_log and now - self.min_log[0] >= 60.0:
                    self.min_log.popleft()

                if (len(self.sec_log) < self.per_second
                        and len(self.min_log) < self.per_minute):
                    self.sec_log.append(now)
                    self.min_log.append(now)
                    return

                wait_sec = 0.0
                if len(self.sec_log) >= self.per_second:
                    wait_sec = max(wait_sec, 1.0 - (now - self.sec_log[0]))
                if len(self.min_log) >= self.per_minute:
                    wait_sec = max(wait_sec, 60.0 - (now - self.min_log[0]))
                wait_sec = max(wait_sec, 0.01)

            print(f"[RATE] throttling, wait {wait_sec:.2f}s "
                  f"(sec={len(self.sec_log)}/{self.per_second}, "
                  f"min={len(self.min_log)}/{self.per_minute})")
            await asyncio.sleep(wait_sec)


limiter = SlidingWindowLimiter(MAX_PER_SECOND, MAX_PER_MINUTE)


# ---------- Think stripping ----------
def strip_think_text(text: str) -> str:
    if not text:
        return text
    text = THINK_PATTERN.sub("", text)
    if "<think>" in text and "</think>" not in text:
        text = text.split("<think>")[0]
    return text.lstrip()


def strip_think_in_response_json(data: dict) -> dict:
    for choice in data.get("choices", []):
        msg = choice.get("message") or {}
        if "content" in msg and isinstance(msg["content"], str):
            msg["content"] = strip_think_text(msg["content"])
        if "reasoning_content" in msg:
            msg["reasoning_content"] = None
    return data


# ---------- Forwarding ----------
async def forward(path: str, request: Request):
    target_url = f"{THAILLM_BASE_URL}/{path}"
    body = await request.body()

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "authorization", "content-length", "apikey")
    }
    headers["Authorization"] = f"Bearer {THAILLM_API_KEY}"

    is_stream = b'"stream":true' in body.replace(b" ", b"") or b'"stream": true' in body
    print(f"[PROXY] {request.method} {target_url}  (stream={is_stream})")

    await limiter.acquire()

    if is_stream:
        return await forward_stream(request, target_url, body, headers)
    return await forward_nonstream(request, target_url, body, headers, path)


async def forward_stream(request: Request, target_url: str, body: bytes, headers: dict):
    """Streaming passthrough with think-tag filtering."""

    async def stream_generator():
        in_think = False
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                request.method, target_url,
                content=body, headers=headers,
                params=request.query_params,
            ) as upstream:
                if upstream.status_code == 429:
                    print("[PROXY] upstream 429, giving up on stream")
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                    return

                async for line in upstream.aiter_lines():
                    if not line:
                        yield b"\n"
                        continue

                    if not STRIP_THINK or not line.startswith("data: "):
                        yield (line + "\n").encode("utf-8")
                        continue

                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        yield (line + "\n").encode("utf-8")
                        continue

                    try:
                        obj = json.loads(payload)
                        for ch in obj.get("choices", []):
                            delta = ch.get("delta") or {}
                            content = delta.get("content")
                            if not content:
                                continue
                            out = ""
                            i = 0
                            while i < len(content):
                                if in_think:
                                    end = content.find("</think>", i)
                                    if end == -1:
                                        i = len(content)
                                    else:
                                        i = end + len("</think>")
                                        in_think = False
                                else:
                                    start = content.find("<think>", i)
                                    if start == -1:
                                        out += content[i:]
                                        i = len(content)
                                    else:
                                        out += content[i:start]
                                        i = start + len("<think>")
                                        in_think = True
                            delta["content"] = out
                        yield f"data: {json.dumps(obj, ensure_ascii=False)}\n".encode("utf-8")
                    except Exception:
                        yield (line + "\n").encode("utf-8")

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


async def forward_nonstream(
    request: Request, target_url: str, body: bytes, headers: dict, path: str
):
    """Non-streaming: auto-retry on 429."""
    for attempt in range(1, MAX_RETRY_ON_429 + 1):
        async with httpx.AsyncClient(timeout=120.0) as client:
            upstream = await client.request(
                request.method, target_url,
                content=body, headers=headers,
                params=request.query_params,
            )

        if upstream.status_code == 429 and attempt < MAX_RETRY_ON_429:
            retry_after = upstream.headers.get("retry-after")
            wait = float(retry_after) if retry_after else (2 ** attempt)
            print(f"[PROXY] 429 upstream, retry {attempt}/{MAX_RETRY_ON_429} after {wait}s")
            await asyncio.sleep(wait)
            await limiter.acquire()
            continue

        if upstream.status_code >= 400:
            print(f"[PROXY] ERROR {upstream.status_code}: {upstream.text[:500]}")
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
            )

        if STRIP_THINK and "chat/completions" in path:
            try:
                data = json.loads(upstream.content)
                data = strip_think_in_response_json(data)
                return Response(
                    content=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    status_code=upstream.status_code,
                    media_type="application/json",
                )
            except Exception as e:
                print(f"[PROXY] strip error: {e}")

        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    return Response(
        content=b'{"error":"upstream rate limited after retries"}',
        status_code=429,
        media_type="application/json",
    )


# ---------- Routes ----------
@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_v1(path: str, request: Request):
    return await forward(path, request)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_root(path: str, request: Request):
    return await forward(path, request)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "upstream": THAILLM_BASE_URL,
        "strip_think": STRIP_THINK,
        "rate_limits": {
            "per_second": MAX_PER_SECOND,
            "per_minute": MAX_PER_MINUTE,
            "current_sec": len(limiter.sec_log),
            "current_min": len(limiter.min_log),
        },
    }


if __name__ == "__main__":
    print(f"ThaiLLM Proxy running on http://{PROXY_HOST}:{PROXY_PORT}")
    print(f"Forwarding to: {THAILLM_BASE_URL}")
    print(f"Strip <think>: {STRIP_THINK}")
    print(f"Rate limit: {MAX_PER_SECOND}/sec, {MAX_PER_MINUTE}/min")
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="info")
