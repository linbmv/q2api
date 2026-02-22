import json
from typing import Dict, Any
from fastapi import HTTPException

try:
    import tiktoken
    ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:
    tiktoken = None
    ENCODING = None

_GENERIC_HTTP_DETAILS: Dict[int, str] = {
    400: "Bad request",
    401: "Unauthorized",
    404: "Not found",
    408: "Request timeout",
    422: "Invalid request",
    429: "Too many requests",
    500: "Internal server error",
    502: "Upstream error",
    503: "Service unavailable",
}

def _generic_http_detail(status_code: int) -> str:
    return _GENERIC_HTTP_DETAILS.get(status_code, "Request failed")

def _validated_status_code(code: Any, *, default: int = 502) -> int:
    try:
        code_int = int(code)
    except Exception:
        return default
    return code_int if 100 <= code_int <= 599 else default

def _is_quota_error(exc: BaseException) -> bool:
    if isinstance(exc, HTTPException) and exc.status_code == 429:
        return True
    if hasattr(exc, 'response') and hasattr(exc.response, 'status_code'):
        if exc.response.status_code == 429:
            return True
    status = getattr(exc, 'status_code', None) or getattr(exc, 'status', None)
    if status == 429:
        return True
    err_msg = str(exc).lower()
    return '429' in err_msg or 'rate limit' in err_msg or 'quota' in err_msg

def count_tokens(text: str, apply_multiplier: bool = False) -> int:
    from app.config import TOKEN_COUNT_MULTIPLIER
    if not text:
        return 0
    if not ENCODING:
        token_count = len(text) // 4
    else:
        token_count = len(ENCODING.encode(text))
    if apply_multiplier:
        token_count = int(token_count * TOKEN_COUNT_MULTIPLIER)
    return token_count

def _sse_format(obj: Dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
