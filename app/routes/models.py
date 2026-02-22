from typing import Dict, Any
from fastapi import APIRouter, Depends

from app.middleware.auth import require_account

router = APIRouter()

@router.get("/v1/models")
async def list_models(account: Dict[str, Any] = Depends(require_account)):
    supported_models = [
        {"id": "claude-sonnet-4.5", "object": "model", "created": 1727740800, "owned_by": "anthropic"},
        {"id": "claude-haiku-4.5", "object": "model", "created": 1730419200, "owned_by": "anthropic"},
        {"id": "claude-opus-4.5", "object": "model", "created": 1730419200, "owned_by": "anthropic"},
        {"id": "claude-opus-4.6", "object": "model", "created": 1740000000, "owned_by": "anthropic"},
        {"id": "claude-sonnet-4-5-20250929", "object": "model", "created": 1727740800, "owned_by": "anthropic"},
        {"id": "claude-opus-4-5-20251101", "object": "model", "created": 1730419200, "owned_by": "anthropic"},
        {"id": "claude-opus-4-6", "object": "model", "created": 1740000000, "owned_by": "anthropic"},
        {"id": "claude-3-5-sonnet-20241022", "object": "model", "created": 1729555200, "owned_by": "anthropic"},
        {"id": "claude-3-5-sonnet-20240620", "object": "model", "created": 1718841600, "owned_by": "anthropic"},
    ]
    return {"object": "list", "data": supported_models}
