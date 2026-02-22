import time
import uuid
from typing import Dict, Any, List

import httpx
from fastapi import HTTPException

from db import row_to_dict
from account_pool import get_pool
from app.config import MAX_ERROR_COUNT, OIDC_BASE, TOKEN_URL
from app.http_client import get_http_client
from app.utils import _generic_http_detail

def _oidc_headers() -> Dict[str, str]:
    return {
        "content-type": "application/json",
        "user-agent": "aws-sdk-rust/1.3.9 os/macos lang/rust/1.87.0 exec-env/CLI md/appVersion-1.19.7",
        "x-amz-user-agent": "aws-sdk-rust/1.3.9 ua/2.1 api/ssooidc/1.88.0 os/macos lang/rust/1.87.0 exec-env/CLI m/E md/appVersion-1.19.7 app/AmazonQ-For-CLI",
        "amz-sdk-request": "attempt=1; max=3",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
    }

async def _get_db():
    from db import get_db
    return get_db()

async def list_enabled_accounts() -> List[Dict[str, Any]]:
    db = await _get_db()
    rows = await db.fetchall("SELECT * FROM accounts WHERE enabled=1")
    return [row_to_dict(r) for r in rows]

async def get_account(account_id: str) -> Dict[str, Any]:
    db = await _get_db()
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not row:
        raise HTTPException(status_code=404, detail=_generic_http_detail(404))
    return row_to_dict(row)

async def refresh_access_token_in_db(account_id: str) -> Dict[str, Any]:
    db = await _get_db()
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not row:
        raise HTTPException(status_code=404, detail=_generic_http_detail(404))
    acc = row_to_dict(row)

    if not acc.get("clientId") or not acc.get("clientSecret") or not acc.get("refreshToken"):
        raise HTTPException(status_code=400, detail=_generic_http_detail(400))

    payload = {
        "grantType": "refresh_token",
        "clientId": acc["clientId"],
        "clientSecret": acc["clientSecret"],
        "refreshToken": acc["refreshToken"],
    }

    try:
        client = get_http_client()
        if not client:
            async with httpx.AsyncClient(timeout=60.0) as temp_client:
                r = await temp_client.post(TOKEN_URL, headers=_oidc_headers(), json=payload)
                r.raise_for_status()
                data = r.json()
        else:
            r = await client.post(TOKEN_URL, headers=_oidc_headers(), json=payload)
            r.raise_for_status()
            data = r.json()

        new_access = data.get("accessToken")
        new_refresh = data.get("refreshToken", acc.get("refreshToken"))
        expires_in = int(data.get("expiresIn", 3600))
        expires_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + expires_in))
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        status = "success"
    except httpx.HTTPError:
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        await db.execute(
            "UPDATE accounts SET last_refresh_time=?, last_refresh_status=?, updated_at=? WHERE id=?",
            (now, "failed", now, account_id),
        )
        await update_stats(account_id, False)
        raise HTTPException(status_code=502, detail=_generic_http_detail(502))
    except Exception:
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        await db.execute(
            "UPDATE accounts SET last_refresh_time=?, last_refresh_status=?, updated_at=? WHERE id=?",
            (now, "failed", now, account_id),
        )
        await update_stats(account_id, False)
        raise

    await db.execute(
        "UPDATE accounts SET accessToken=?, refreshToken=?, expires_at=?, last_refresh_time=?, last_refresh_status=?, updated_at=? WHERE id=?",
        (new_access, new_refresh, expires_at, now, status, now, account_id),
    )

    row2 = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
    return row_to_dict(row2)

async def update_stats(
    account_id: str,
    success: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    is_quota_error: bool = False
) -> None:
    pool = get_pool()
    db = await _get_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    if success:
        await pool.record_success(account_id, input_tokens, output_tokens)
        await db.execute(
            """UPDATE accounts SET
                success_count=COALESCE(success_count,0)+1,
                request_count=COALESCE(request_count,0)+1,
                error_count=0,
                total_tokens=COALESCE(total_tokens,0)+?,
                total_input_tokens=COALESCE(total_input_tokens,0)+?,
                total_output_tokens=COALESCE(total_output_tokens,0)+?,
                last_used_at=?,
                updated_at=?
            WHERE id=?""",
            (input_tokens + output_tokens, input_tokens, output_tokens, now, now, account_id)
        )
    else:
        await pool.record_error(account_id, is_quota_error=is_quota_error)
        await db.execute(
            """UPDATE accounts SET
                error_count=COALESCE(error_count,0)+1,
                request_count=COALESCE(request_count,0)+1,
                enabled=CASE WHEN COALESCE(error_count,0)+1>=? THEN 0 ELSE enabled END,
                last_used_at=?,
                updated_at=?
            WHERE id=?""",
            (MAX_ERROR_COUNT, now, now, account_id)
        )
