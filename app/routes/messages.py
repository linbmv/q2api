import json
import uuid
import re
import asyncio
import traceback
from typing import Dict, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from app.middleware.auth import require_account
from app.utils import count_tokens, _sse_format, _generic_http_detail, _validated_status_code, _is_quota_error
from app.http_client import get_http_client
from app.services.accounts import refresh_access_token_in_db, update_stats
from app.services.claude import (
    get_claude_request_class,
    get_stream_handler_class,
    convert_claude_to_amazonq_request,
    map_model_name,
    send_chat_request
)
from message_processor import process_history_for_amazonq, merge_duplicate_tool_results

router = APIRouter()

ClaudeRequest = get_claude_request_class()

@router.post("/v1/messages")
async def claude_messages(req: ClaudeRequest, account: Dict[str, Any] = Depends(require_account)):
    text_to_count = ""
    if req.system:
        if isinstance(req.system, str):
            text_to_count += req.system
        elif isinstance(req.system, list):
            for item in req.system:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")

    for msg in req.messages:
        if isinstance(msg.content, str):
            text_to_count += msg.content
        elif isinstance(msg.content, list):
            for item in msg.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")

    if req.tools:
        text_to_count += json.dumps([tool.model_dump() if hasattr(tool, 'model_dump') else tool for tool in req.tools], ensure_ascii=False)

    input_tokens = count_tokens(text_to_count, apply_multiplier=True)

    if input_tokens > 150000:
        error_message = f"Context too long: {input_tokens} tokens exceeds the 150,000 token limit. Please compress your context and retry."
        if req.stream:
            async def error_stream():
                yield _sse_format({"type": "message_start", "message": {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant", "content": [], "model": req.model, "stop_reason": None, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}})
                yield _sse_format({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
                yield _sse_format({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": error_message}})
                yield _sse_format({"type": "content_block_stop", "index": 0})
                yield _sse_format({"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 0}})
                yield _sse_format({"type": "message_stop"})
            return StreamingResponse(error_stream(), media_type="text/event-stream")
        else:
            return {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant", "content": [{"type": "text", "text": error_message}], "model": req.model, "stop_reason": "max_tokens", "usage": {"input_tokens": input_tokens, "output_tokens": len(error_message) // 4}}

    if input_tokens > 120000 and len(req.messages) > 4:
        req.messages = req.messages[-6:]
        text_to_count = ""
        if req.system:
            if isinstance(req.system, str):
                text_to_count += req.system
            elif isinstance(req.system, list):
                for item in req.system:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_to_count += item.get("text", "")
        for msg in req.messages:
            if isinstance(msg.content, str):
                text_to_count += msg.content
            elif isinstance(msg.content, list):
                for item in msg.content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_to_count += item.get("text", "")
        input_tokens = count_tokens(text_to_count, apply_multiplier=True)

    try:
        aq_request = convert_claude_to_amazonq_request(req)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=_generic_http_detail(400))

    try:
        conversation_state = aq_request.get("conversationState", {})
        conversation_id = conversation_state.get("conversationId") or str(uuid.uuid4())
        history = conversation_state.get("history", [])
        if history:
            processed_history = process_history_for_amazonq(history)
            conversation_state["history"] = processed_history
            aq_request["conversationState"] = conversation_state
        current_message = conversation_state.get("currentMessage", {})
        user_input_message = current_message.get("userInputMessage", {})
        user_input_message_context = user_input_message.get("userInputMessageContext", {})
        tool_results = user_input_message_context.get("toolResults", [])
        if tool_results:
            merged_tool_results = merge_duplicate_tool_results(tool_results)
            user_input_message_context["toolResults"] = merged_tool_results
            user_input_message["userInputMessageContext"] = user_input_message_context
            current_message["userInputMessage"] = user_input_message
            conversation_state["currentMessage"] = current_message
            aq_request["conversationState"] = conversation_state
    except Exception as e:
        traceback.print_exc()
        print(f"Warning: Post-processing failed: {e}")

    event_iter = None
    try:
        access = account.get("accessToken")
        if not access:
            refreshed = await refresh_access_token_in_db(account["id"])
            access = refreshed.get("accessToken")

        client = get_http_client()
        try:
            _, _, tracker, event_iter = await send_chat_request(
                access_token=access, messages=[], model=map_model_name(req.model)[0],
                stream=True, client=client, raw_payload=aq_request
            )
        except httpx.HTTPError as e:
            error_msg = str(e)
            if "Upstream error" in error_msg and "500" in error_msg:
                raise HTTPException(status_code=400, detail=_generic_http_detail(400))
            raise HTTPException(status_code=502, detail=_generic_http_detail(502))

        if not event_iter:
            raise HTTPException(status_code=502, detail=_generic_http_detail(502))

        text_to_count = ""
        if req.system:
            if isinstance(req.system, str):
                text_to_count += req.system
            elif isinstance(req.system, list):
                for item in req.system:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_to_count += item.get("text", "")
        for msg in req.messages:
            if isinstance(msg.content, str):
                text_to_count += msg.content
            elif isinstance(msg.content, list):
                for item in msg.content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_to_count += item.get("text", "")
        input_tokens = count_tokens(text_to_count, apply_multiplier=True)

        ClaudeStreamHandler = get_stream_handler_class()
        handler = ClaudeStreamHandler(model=req.model, input_tokens=input_tokens, conversation_id=conversation_id)

        first_event = None
        try:
            first_event = await event_iter.__anext__()
        except StopAsyncIteration:
            raise HTTPException(status_code=502, detail=_generic_http_detail(502))
        except Exception as e:
            err_msg = str(e)
            if err_msg.startswith("Upstream error "):
                match = re.match(r"Upstream error (\d+):", err_msg)
                if match:
                    status_code = _validated_status_code(match.group(1), default=502)
                    raise HTTPException(status_code=status_code, detail=_generic_http_detail(status_code))
            raise HTTPException(status_code=502, detail=_generic_http_detail(502))

        async def event_generator():
            try:
                if first_event:
                    event_type, payload = first_event
                    async for sse in handler.handle_event(event_type, payload):
                        yield sse
                async for event_type, payload in event_iter:
                    async for sse in handler.handle_event(event_type, payload):
                        yield sse
                async for sse in handler.finish():
                    yield sse
                await update_stats(account["id"], True, input_tokens=handler.input_tokens, output_tokens=handler.output_tokens)
            except GeneratorExit:
                has_content = tracker.has_content if tracker else False
                if has_content:
                    await update_stats(account["id"], True, input_tokens=handler.input_tokens, output_tokens=handler.output_tokens)
            except asyncio.CancelledError:
                has_content = tracker.has_content if tracker else False
                if has_content:
                    await update_stats(account["id"], True, input_tokens=handler.input_tokens, output_tokens=handler.output_tokens)
            except Exception as e:
                await update_stats(account["id"], False, is_quota_error=_is_quota_error(e))
                raise

        if req.stream:
            return StreamingResponse(event_generator(), media_type="text/event-stream")
        else:
            final_content = []
            usage = {"input_tokens": 0, "output_tokens": 0}
            stop_reason = None
            async for sse_chunk in event_generator():
                data_str = None
                for line in sse_chunk.strip().split('\n'):
                    if line.startswith("data:"):
                        data_str = line[6:].strip()
                        break
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                    dtype = data.get("type")
                    if dtype == "content_block_start":
                        idx = data.get("index", 0)
                        while len(final_content) <= idx:
                            final_content.append(None)
                        final_content[idx] = data.get("content_block")
                    elif dtype == "content_block_delta":
                        idx = data.get("index", 0)
                        delta = data.get("delta", {})
                        if final_content[idx]:
                            if delta.get("type") == "text_delta":
                                final_content[idx]["text"] += delta.get("text", "")
                            elif delta.get("type") == "thinking_delta":
                                final_content[idx].setdefault("thinking", "")
                                final_content[idx]["thinking"] += delta.get("thinking", "")
                            elif delta.get("type") == "input_json_delta":
                                if "partial_json" not in final_content[idx]:
                                    final_content[idx]["partial_json"] = ""
                                final_content[idx]["partial_json"] += delta.get("partial_json", "")
                    elif dtype == "content_block_stop":
                        idx = data.get("index", 0)
                        if final_content[idx] and final_content[idx].get("type") == "tool_use":
                            if "partial_json" in final_content[idx]:
                                try:
                                    final_content[idx]["input"] = json.loads(final_content[idx]["partial_json"])
                                except json.JSONDecodeError:
                                    final_content[idx]["input"] = {"error": "invalid json", "partial": final_content[idx]["partial_json"]}
                                del final_content[idx]["partial_json"]
                    elif dtype == "message_delta":
                        usage = data.get("usage", usage)
                        stop_reason = data.get("delta", {}).get("stop_reason")
                except json.JSONDecodeError:
                    pass
                except Exception:
                    traceback.print_exc()
            final_content_cleaned = [c for c in final_content if c is not None]
            for c in final_content_cleaned:
                c.pop("partial_json", None)
            return JSONResponse(content={"id": f"msg_{uuid.uuid4()}", "type": "message", "role": "assistant", "model": req.model, "content": final_content_cleaned, "stop_reason": stop_reason, "stop_sequence": None, "usage": usage})

    except Exception as e:
        try:
            if event_iter and hasattr(event_iter, "aclose"):
                await event_iter.aclose()
        except Exception:
            pass
        await update_stats(account["id"], False, is_quota_error=_is_quota_error(e))
        err_msg = str(e)
        if err_msg.startswith("Upstream error "):
            match = re.match(r"Upstream error (\d+):", err_msg)
            if match:
                status_code = _validated_status_code(match.group(1), default=502)
                raise HTTPException(status_code=status_code, detail=_generic_http_detail(status_code))
        raise

@router.post("/v1/messages/count_tokens")
async def count_tokens_endpoint(req: ClaudeRequest):
    text_to_count = ""
    if req.system:
        if isinstance(req.system, str):
            text_to_count += req.system
        elif isinstance(req.system, list):
            for item in req.system:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")
    for msg in req.messages:
        if isinstance(msg.content, str):
            text_to_count += msg.content
        elif isinstance(msg.content, list):
            for item in msg.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")
    if req.tools:
        text_to_count += json.dumps([tool.model_dump() if hasattr(tool, 'model_dump') else tool for tool in req.tools], ensure_ascii=False)
    input_tokens = count_tokens(text_to_count, apply_multiplier=True)
    return {"input_tokens": input_tokens}
