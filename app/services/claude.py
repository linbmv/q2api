import importlib.util
from pathlib import Path
from typing import Tuple

from app.config import BASE_DIR

def _load_module(name: str, filename: str):
    mod_path = BASE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, str(mod_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_replicate = None
_claude_types = None
_claude_converter = None
_claude_stream = None

def _ensure_modules():
    global _replicate, _claude_types, _claude_converter, _claude_stream
    if _replicate is None:
        _replicate = _load_module("v2_replicate", "replicate.py")
    if _claude_types is None:
        _claude_types = _load_module("v2_claude_types", "claude_types.py")
        import sys
        sys.modules["v2.claude_types"] = _claude_types
    if _claude_converter is None:
        _claude_converter = _load_module("v2_claude_converter", "claude_converter.py")
    if _claude_stream is None:
        _claude_stream = _load_module("v2_claude_stream", "claude_stream.py")

def get_claude_request_class():
    _ensure_modules()
    return _claude_types.ClaudeRequest

def get_stream_handler_class():
    _ensure_modules()
    return _claude_stream.ClaudeStreamHandler

def convert_claude_to_amazonq_request(req):
    _ensure_modules()
    return _claude_converter.convert_claude_to_amazonq_request(req)

def map_model_name(model: str) -> Tuple[str, bool]:
    _ensure_modules()
    return _claude_converter.map_model_name(model)

async def send_chat_request(access_token, messages, model, stream, client, raw_payload):
    _ensure_modules()
    return await _replicate.send_chat_request(
        access_token=access_token,
        messages=messages,
        model=model,
        stream=stream,
        client=client,
        raw_payload=raw_payload
    )
