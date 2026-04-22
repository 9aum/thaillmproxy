import os

from dotenv import load_dotenv

load_dotenv()

THAILLM_BASE_URL: str = os.getenv("THAILLM_BASE_URL", "http://thaillm.or.th/api/v1")
THAILLM_API_KEY: str = os.environ["THAILLM_API_KEY"]  # required — raises KeyError if missing
PROXY_HOST: str = os.getenv("PROXY_HOST", "127.0.0.1")
PROXY_PORT: int = int(os.getenv("PROXY_PORT", "4000"))
STRIP_THINK: bool = os.getenv("STRIP_THINK", "false").lower() == "true"

MAX_PER_SECOND: int = int(os.getenv("MAX_PER_SECOND", "4"))
MAX_PER_MINUTE: int = int(os.getenv("MAX_PER_MINUTE", "180"))
MAX_RETRY_ON_429: int = int(os.getenv("MAX_RETRY_ON_429", "3"))
