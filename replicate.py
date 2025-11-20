import json
import uuid
import os
import asyncio
import weakref
import logging
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, Iterator, List, AsyncGenerator, Any, Set
import struct
import httpx
import importlib.util

# Amazon Q 支持的规范模型 ID 白名单
ALLOWED_CANONICAL_MODELS: Set[str] = {
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "claude-sonnet-3.5",
}

# 默认规范模型（验证有效性）
DEFAULT_CANONICAL_MODEL = os.getenv("DEFAULT_MODEL_ID", "claude-sonnet-4.5")
if DEFAULT_CANONICAL_MODEL not in ALLOWED_CANONICAL_MODELS:
    logging.warning(
        "DEFAULT_MODEL_ID '%s' not in allowed set; falling back to 'claude-sonnet-4.5'",
        DEFAULT_CANONICAL_MODEL,
    )
    DEFAULT_CANONICAL_MODEL = "claude-sonnet-4.5"

# 模型名称映射表
MODEL_MAPPING: Dict[str, str] = {
    # Claude 4.5
    "claude-sonnet-4-5-20250929": "claude-sonnet-4.5",
    "claude-haiku-4-5-20251001": "claude-sonnet-4.5",
    # Claude 4
    "claude-sonnet-4-20250514": "claude-sonnet-4",
    # Claude 3.5
    "claude-3-5-sonnet-20241022": "claude-sonnet-3.5",
    "claude-3-5-haiku-20241022": "claude-sonnet-3.5",
}

# 记录已警告的未映射模型，避免日志泛滥
_WARNED_UNMAPPED: Set[str] = set()

def canonicalize_model_name(model: str) -> str:
    """将模型名称规范化为 Amazon Q 接受的格式"""
    # 归一化：去空白、转小写、统一分隔符
    norm = re.sub(r"[\s_]+", "-", model.strip().lower())

    # 精确映射表查找
    mapped = MODEL_MAPPING.get(norm)
    if mapped:
        return mapped

    # 模式匹配：支持未来的日期后缀变体
    if re.match(r"^claude-(?:sonnet|haiku)-4-5-\d{8}$", norm):
        return "claude-sonnet-4.5"
    if re.match(r"^claude-sonnet-4-\d{8}$", norm):
        return "claude-sonnet-4"
    if re.match(r"^claude-3-5-(?:sonnet|haiku)-\d{8}$", norm):
        return "claude-sonnet-3.5"

    # 如果已经是规范名称，保持不变
    if norm in ALLOWED_CANONICAL_MODELS:
        return norm

    # 返回归一化后的原始名称（由调用者检查）
    return norm

def _load_claude_parser():
    """Dynamically load claude_parser module."""
    base_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("v2_claude_parser", str(base_dir / "claude_parser.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

try:
    _parser = _load_claude_parser()
    EventStreamParser = _parser.EventStreamParser
    extract_event_info = _parser.extract_event_info
except Exception as e:
    print(f"Warning: Failed to load claude_parser: {e}")
    EventStreamParser = None
    extract_event_info = None

class StreamTracker:
    def __init__(self):
        self.has_content = False
    
    async def track(self, gen: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        async for item in gen:
            if item:
                self.has_content = True
            yield item

def _get_proxies() -> Optional[Dict[str, str]]:
    proxy = os.getenv("HTTP_PROXY", "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return None

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "streaming_request.json"

def load_template() -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    url, headers, body = data
    assert isinstance(url, str) and isinstance(headers, dict) and isinstance(body, dict)
    return url, headers, body

def _merge_headers(as_log: Dict[str, str], bearer_token: str) -> Dict[str, str]:
    headers = dict(as_log)
    for k in list(headers.keys()):
        kl = k.lower()
        if kl in ("content-length","host","connection","transfer-encoding"):
            headers.pop(k, None)
    def set_header(name: str, value: str):
        for key in list(headers.keys()):
            if key.lower() == name.lower():
                del headers[key]
        headers[name] = value
    set_header("Authorization", f"Bearer {bearer_token}")
    set_header("amz-sdk-invocation-id", str(uuid.uuid4()))
    return headers

def _parse_event_headers(raw: bytes) -> Dict[str, object]:
    headers: Dict[str, object] = {}
    i = 0
    n = len(raw)
    while i < n:
        if i + 1 > n:
            break
        name_len = raw[i]
        i += 1
        if i + name_len + 1 > n:
            break
        name = raw[i : i + name_len].decode("utf-8", errors="ignore")
        i += name_len
        htype = raw[i]
        i += 1
        if htype == 0:
            val = True
        elif htype == 1:
            val = False
        elif htype == 2:
            if i + 1 > n: break
            val = raw[i]; i += 1
        elif htype == 3:
            if i + 2 > n: break
            val = int.from_bytes(raw[i:i+2],"big",signed=True); i += 2
        elif htype == 4:
            if i + 4 > n: break
            val = int.from_bytes(raw[i:i+4],"big",signed=True); i += 4
        elif htype == 5:
            if i + 8 > n: break
            val = int.from_bytes(raw[i:i+8],"big",signed=True); i += 8
        elif htype == 6:
            if i + 2 > n: break
            l = int.from_bytes(raw[i:i+2],"big"); i += 2
            if i + l > n: break
            val = raw[i:i+l]; i += l
        elif htype == 7:
            if i + 2 > n: break
            l = int.from_bytes(raw[i:i+2],"big"); i += 2
            if i + l > n: break
            val = raw[i:i+l].decode("utf-8", errors="ignore"); i += l
        elif htype == 8:
            if i + 8 > n: break
            val = int.from_bytes(raw[i:i+8],"big",signed=False); i += 8
        elif htype == 9:
            if i + 16 > n: break
            import uuid as _uuid
            val = str(_uuid.UUID(bytes=bytes(raw[i:i+16]))); i += 16
        else:
            break
        headers[name] = val
    return headers

class AwsEventStreamParser:
    def __init__(self):
        self._buf = bytearray()
    def feed(self, data: bytes) -> List[Tuple[Dict[str, object], bytes]]:
        if not data:
            return []
        self._buf.extend(data)
        out: List[Tuple[Dict[str, object], bytes]] = []
        while True:
            if len(self._buf) < 12:
                break
            total_len, headers_len, _prelude_crc = struct.unpack(">I I I", self._buf[:12])
            if total_len < 16 or headers_len > total_len:
                self._buf.pop(0)
                continue
            if len(self._buf) < total_len:
                break
            msg = bytes(self._buf[:total_len])
            del self._buf[:total_len]
            headers_raw = msg[12:12+headers_len]
            payload = msg[12+headers_len: total_len-4]
            headers = _parse_event_headers(headers_raw)
            out.append((headers, payload))
        return out

def _try_decode_event_payload(payload: bytes) -> Optional[dict]:
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None

def _extract_text_from_event(ev: dict) -> Optional[str]:
    for key in ("assistantResponseEvent","assistantMessage","message","delta","data"):
        if key in ev and isinstance(ev[key], dict):
            inner = ev[key]
            if isinstance(inner.get("content"), str) and inner.get("content"):
                return inner["content"]
    if isinstance(ev.get("content"), str) and ev.get("content"):
        return ev["content"]
    for list_key in ("chunks","content"):
        if isinstance(ev.get(list_key), list):
            buf = []
            for item in ev[list_key]:
                if isinstance(item, dict):
                    if isinstance(item.get("content"), str):
                        buf.append(item["content"])
                    elif isinstance(item.get("text"), str):
                        buf.append(item["text"])
                elif isinstance(item, str):
                    buf.append(item)
            if buf:
                return "".join(buf)
    for k in ("text","delta","payload"):
        v = ev.get(k)
        if isinstance(v, str) and v:
            return v
    return None

def openai_messages_to_text(messages: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for m in messages:
        role = m.get("role","user")
        content = m.get("content","")
        if isinstance(content, list):
            parts = []
            for seg in content:
                if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                    parts.append(seg["text"])
                elif isinstance(seg, str):
                    parts.append(seg)
            content = "\n".join(parts)
        elif not isinstance(content, str):
            content = str(content)
        lines.append(f"{role}:\n{content}")
    return "\n\n".join(lines)

def inject_history(body_json: Dict[str, Any], history_text: str) -> None:
    try:
        cur = body_json["conversationState"]["currentMessage"]["userInputMessage"]
        content = cur.get("content","")
        if isinstance(content, str):
            cur["content"] = content.replace("你好，你必须讲个故事", history_text)
    except Exception:
        pass

def inject_model(body_json: Dict[str, Any], model: Optional[str]) -> None:
    if not model:
        return

    # 使用规范化函数转换模型名称
    mapped = canonicalize_model_name(model)

    # 白名单校验 + 兜底
    if mapped not in ALLOWED_CANONICAL_MODELS:
        # 避免重复警告同一个模型
        norm = re.sub(r"[\s_]+", "-", model.strip().lower())
        if norm not in _WARNED_UNMAPPED:
            logging.warning(
                "Model '%s' mapped to '%s' not in allowed set; falling back to default '%s'",
                model, mapped, DEFAULT_CANONICAL_MODEL,
            )
            _WARNED_UNMAPPED.add(norm)
        mapped = DEFAULT_CANONICAL_MODEL

    # 写入模型 ID
    try:
        cur = body_json["conversationState"]["currentMessage"]["userInputMessage"]
        if cur.get("modelId") != mapped:
            cur["modelId"] = mapped
            logging.debug("Model '%s' mapped to canonical '%s'", model, mapped)
    except Exception as e:
        logging.error("Failed to inject modelId: %s", e, exc_info=True)

async def send_chat_request(
    access_token: str,
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    stream: bool = False,
    timeout: Tuple[int,int] = (15,300),
    client: Optional[httpx.AsyncClient] = None,
    raw_payload: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], Optional[AsyncGenerator[str, None]], StreamTracker, Optional[AsyncGenerator[Any, None]]]:
    url, headers_from_log, body_json = load_template()
    headers_from_log["amz-sdk-invocation-id"] = str(uuid.uuid4())
    
    if raw_payload:
        # Use raw payload if provided (for Claude API)
        body_json = raw_payload
        # Ensure conversationId is set if missing
        if "conversationState" in body_json and "conversationId" not in body_json["conversationState"]:
             body_json["conversationState"]["conversationId"] = str(uuid.uuid4())
    else:
        # Standard OpenAI-compatible logic
        try:
            body_json["conversationState"]["conversationId"] = str(uuid.uuid4())
        except Exception:
            pass
        history_text = openai_messages_to_text(messages)
        inject_history(body_json, history_text)
        inject_model(body_json, model)

    payload_str = json.dumps(body_json, ensure_ascii=False)
    headers = _merge_headers(headers_from_log, access_token)
    
    local_client = False
    if client is None:
        local_client = True
        proxies = _get_proxies()
        mounts = None
        if proxies:
            proxy_url = proxies.get("https") or proxies.get("http")
            if proxy_url:
                mounts = {
                    "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                    "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                }
        client = httpx.AsyncClient(mounts=mounts, timeout=httpx.Timeout(timeout[0], read=timeout[1]))
    
    # Use manual request sending to control stream lifetime
    req = client.build_request("POST", url, headers=headers, content=payload_str)
    
    resp = None
    try:
        resp = await client.send(req, stream=True)
        
        if resp.status_code >= 400:
            import logging

            # Read error body safely in async streaming mode
            try:
                raw = await resp.aread()
                encoding = resp.charset_encoding or "utf-8"
                err_text = raw.decode(encoding, errors="replace") if raw else "Empty response body"
                err_text = err_text[:8192]  # Limit log size
            except Exception as e:
                err_text = f"Failed to read response body: {e}"

            # Extract upstream diagnostic headers
            req_id = resp.headers.get("x-amzn-requestid") or resp.headers.get("x-amz-request-id")
            err_type = resp.headers.get("x-amzn-errortype")

            logging.error(
                f"Upstream error {resp.status_code} (RequestId={req_id}, ErrType={err_type}): {err_text}"
            )

            await resp.aclose()
            if local_client:
                await client.aclose()
            raise httpx.HTTPError(f"Upstream error {resp.status_code}: {err_text}")
        
        parser = AwsEventStreamParser()
        tracker = StreamTracker()
        
        # Track if the response has been consumed to avoid double-close
        response_consumed = False
        
        async def _iter_events() -> AsyncGenerator[Any, None]:
            nonlocal response_consumed
            try:
                if EventStreamParser and extract_event_info:
                    # Use proper EventStreamParser
                    async def byte_gen():
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                yield chunk
                    
                    async for message in EventStreamParser.parse_stream(byte_gen()):
                        event_info = extract_event_info(message)
                        if event_info:
                            event_type = event_info.get('event_type')
                            payload = event_info.get('payload')
                            if event_type and payload:
                                yield (event_type, payload)
                else:
                    # Fallback to old parser
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        events = parser.feed(chunk)
                        for ev_headers, payload in events:
                            parsed = _try_decode_event_payload(payload)
                            if parsed is not None:
                                event_type = None
                                if ":event-type" in ev_headers:
                                    event_type = ev_headers[":event-type"]
                                yield (event_type, parsed)
            except GeneratorExit:
                # Client disconnected - ensure cleanup without re-raising
                pass
            except Exception:
                if not tracker.has_content:
                    raise
            finally:
                response_consumed = True
                if resp and not resp.is_closed:
                    await resp.aclose()
                if local_client and client:
                    await client.aclose()

        async def _iter_text() -> AsyncGenerator[str, None]:
            async for event_type, parsed in _iter_events():
                text = _extract_text_from_event(parsed)
                if isinstance(text, str) and text:
                    yield text
        
        def _schedule_cleanup():
            """Schedule cleanup when generator is GC'd without being consumed.
            - If there's a running loop: spawn tasks for aclose()
            - Else: try a synchronous close fallback (best-effort)
            """
            try:
                if not resp:
                    return
                if not getattr(resp, "is_closed", True):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(resp.aclose())
                            if local_client and client:
                                asyncio.create_task(client.aclose())
                        else:
                            # No running loop: best-effort close
                            try:
                                # Prefer async close via a temporary loop
                                asyncio.run(resp.aclose())
                                if local_client and client:
                                    asyncio.run(client.aclose())
                            except Exception:
                                # Fallback to sync close if available
                                if hasattr(resp, "close"):
                                    try:
                                        resp.close()  # type: ignore[attr-defined]
                                    except Exception:
                                        pass
                                if local_client and client and hasattr(client, "close"):
                                    try:
                                        client.close()  # type: ignore[attr-defined]
                                    except Exception:
                                        pass
                    except RuntimeError:
                        # No event loop; best-effort sync close
                        try:
                            asyncio.run(resp.aclose())
                            if local_client and client:
                                asyncio.run(client.aclose())
                        except Exception:
                            if hasattr(resp, "close"):
                                try:
                                    resp.close()  # type: ignore[attr-defined]
                                except Exception:
                                    pass
                            if local_client and client and hasattr(client, "close"):
                                try:
                                    client.close()  # type: ignore[attr-defined]
                                except Exception:
                                    pass
            except Exception:
                # Final safety: swallow exceptions in finalizer
                pass
        
        if stream:
            # If raw_payload is used, we might want the raw event stream
            if raw_payload:
                # Return event stream with finalizer as safety net
                event_gen = _iter_events()
                weakref.finalize(event_gen, _schedule_cleanup)
                return None, None, tracker, event_gen
            
            # Return text stream with finalizer as safety net
            text_gen = tracker.track(_iter_text())
            weakref.finalize(text_gen, _schedule_cleanup)
            return None, text_gen, tracker, None
        else:
            buf = []
            try:
                async for t in tracker.track(_iter_text()):
                    buf.append(t)
            finally:
                # Ensure response is closed even if iteration is incomplete
                if not response_consumed and resp:
                    await resp.aclose()
                    if local_client:
                        await client.aclose()
            return "".join(buf), None, tracker, None

    except Exception:
        # Critical: close response on any exception before generators are created
        if resp and not resp.is_closed:
            await resp.aclose()
        if local_client and client:
            await client.aclose()
        raise