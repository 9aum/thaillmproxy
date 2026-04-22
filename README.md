# ThaiLLM Proxy

OpenAI-compatible proxy สำหรับ [ThaiLLM](http://thaillm.or.th) ให้ใช้กับ tools ที่รองรับ OpenAI API format เช่น PicoClaw / ZeroClaw

## Features

- แปลง `Authorization: Bearer` → `apikey` header ที่ ThaiLLM ต้องการ
- Client-side rate limiting (4 req/s, 180 req/min) เพื่อไม่โดน 429
- Auto-retry เมื่อโดน 429 จาก upstream
- กรอง `<think>...</think>` reasoning blocks (เปิด/ปิดได้)
- รองรับทั้ง streaming และ non-streaming

## Setup

```bash
# 1. Clone
git clone https://github.com/<your-username>/thaillmproxy.git
cd thaillmproxy

# 2. Install dependencies
pip install -r requirements.txt

# 3. ตั้งค่า API key
cp .env.example .env
# แก้ไข .env แล้วใส่ THAILLM_API_KEY ของคุณ

# 4. Run
python proxy.py
```

Proxy จะรันที่ `http://127.0.0.1:4000`

## Configuration

แก้ไขค่าใน `.env`:

| Variable | Default | Description |
|---|---|---|
| `THAILLM_API_KEY` | *required* | API key จาก ThaiLLM |
| `THAILLM_BASE_URL` | `http://thaillm.or.th/api/v1` | ThaiLLM endpoint |
| `PROXY_HOST` | `127.0.0.1` | Host ที่ proxy listen |
| `PROXY_PORT` | `4000` | Port ที่ proxy listen |
| `STRIP_THINK` | `false` | ตั้ง `true` เพื่อกรอง `<think>` blocks |
| `MAX_PER_SECOND` | `4` | Rate limit ต่อวินาที |
| `MAX_PER_MINUTE` | `180` | Rate limit ต่อนาที |
| `MAX_RETRY_ON_429` | `3` | จำนวน retry เมื่อโดน 429 |

## ใช้กับ PicoClaw บน Termux/Ubuntu

ดู [`picoclaw/bashrc.ubuntu.1.txt`](picoclaw/bashrc.ubuntu.1.txt) สำหรับ `.bashrc` snippet ที่จะ:
1. Start proxy ทำงานเบื้องหลังอัตโนมัติ
2. Start PicoClaw gateway

## API Endpoints

Proxy รองรับ endpoint เดียวกับ OpenAI:

```
POST /v1/chat/completions
GET  /v1/models
...
```

ตั้งค่า base URL ใน client ของคุณเป็น `http://127.0.0.1:4000`
