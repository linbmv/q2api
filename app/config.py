import os
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent

OIDC_BASE = "https://oidc.us-east-1.amazonaws.com"
TOKEN_URL = f"{OIDC_BASE}/token"

def _parse_allowed_keys_env() -> List[str]:
    s = os.getenv("OPENAI_KEYS", "") or ""
    keys: List[str] = []
    for k in [x.strip() for x in s.split(",") if x.strip()]:
        keys.append(k)
    return keys

ALLOWED_API_KEYS: List[str] = _parse_allowed_keys_env()
MAX_ERROR_COUNT: int = int(os.getenv("MAX_ERROR_COUNT", "100"))
TOKEN_COUNT_MULTIPLIER: float = float(os.getenv("TOKEN_COUNT_MULTIPLIER", "1.0"))

def _is_console_enabled() -> bool:
    console_env = os.getenv("ENABLE_CONSOLE", "true").strip().lower()
    return console_env not in ("false", "0", "no", "disabled")

CONSOLE_ENABLED: bool = _is_console_enabled()
CONSOLE_TOKEN: str = os.getenv("CONSOLE_TOKEN", "").strip()
