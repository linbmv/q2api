from typing import Optional, Dict, Any
from fastapi import Header, HTTPException

from app.config import ALLOWED_API_KEYS, CONSOLE_TOKEN

def _extract_bearer(token_header: Optional[str]) -> Optional[str]:
    if not token_header:
        return None
    if token_header.startswith("Bearer "):
        return token_header.split(" ", 1)[1].strip()
    return token_header.strip()

async def resolve_account_for_key(key: Optional[str]) -> Dict[str, Any]:
    from account_pool import get_pool
    pool = get_pool()

    if not key:
        if not ALLOWED_API_KEYS:
            account = await pool.get_account()
            if not account:
                raise HTTPException(status_code=503, detail="Service unavailable")
            return account
        raise HTTPException(status_code=401, detail="Unauthorized")

    if ALLOWED_API_KEYS and key not in ALLOWED_API_KEYS:
        raise HTTPException(status_code=401, detail="Unauthorized")

    account = await pool.get_account()
    if not account:
        raise HTTPException(status_code=503, detail="Service unavailable")
    return account

async def require_account(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    key = _extract_bearer(authorization) if authorization else x_api_key
    return await resolve_account_for_key(key)

async def verify_console_token(authorization: Optional[str] = Header(None)) -> bool:
    if not CONSOLE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    bearer = _extract_bearer(authorization)
    if not bearer or bearer != CONSOLE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True
