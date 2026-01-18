import json
import uuid
import time
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Union

import sys

# Try sys.modules first (injected by app.py)
if "v2.claude_types" in sys.modules:
    from v2.claude_types import ClaudeRequest, ClaudeMessage, ClaudeTool
else:
    try:
        from .claude_types import ClaudeRequest, ClaudeMessage, ClaudeTool
    except ImportError:
        try:
            from claude_types import ClaudeRequest, ClaudeMessage, ClaudeTool
        except ImportError:
            pass

logger = logging.getLogger(__name__)

THINKING_HINT = "<antml:thinking_mode>interleaved</antml:thinking_mode><antml:max_thinking_length>16000</antml:max_thinking_length>"
THINKING_START_TAG = "<thinking>"
THINKING_END_TAG = "</thinking>"

def _wrap_thinking_content(thinking_text: str) -> str:
    """Wrap thinking text with the XML tag expected by Amazon Q."""
    return f"{THINKING_START_TAG}{thinking_text}{THINKING_END_TAG}"

def is_thinking_mode_enabled(thinking_cfg: Optional[Any]) -> bool:
    """Detect whether the client enabled thinking mode."""
    if thinking_cfg is None:
        return False
    if isinstance(thinking_cfg, bool):
        return thinking_cfg
    if isinstance(thinking_cfg, str):
        return thinking_cfg.lower() == "enabled"
    if isinstance(thinking_cfg, dict):
        type_val = str(thinking_cfg.get("type", "")).lower()
        if type_val == "enabled":
            return True
        enabled_flag = thinking_cfg.get("enabled")
        if isinstance(enabled_flag, bool):
            return enabled_flag
        budget = thinking_cfg.get("budget_tokens")
        if isinstance(budget, (int, float)) and budget > 0:
            return True
    return False

def _append_thinking_hint(text: str, hint: str = THINKING_HINT) -> str:
    """Append the special hint once to the end of the text."""
    text = text or ""
    normalized = text.rstrip()
    if normalized.endswith(hint):
        return text
    if not text:
        return hint
    separator = "" if text.endswith(("\n", "\r")) else "\n"
    return f"{text}{separator}{hint}"

def get_current_timestamp() -> str:
    """Get current timestamp in Amazon Q format."""
    now = datetime.now().astimezone()
    weekday = now.strftime("%A")
    iso_time = now.isoformat(timespec='milliseconds')
    return f"{weekday}, {iso_time}"

def map_model_name(claude_model: str) -> tuple[str, bool]:
    """Map Claude model name to Amazon Q model ID.

    Accepts both short names (e.g., claude-sonnet-4) and canonical names
    (e.g., claude-sonnet-4-20250514).

    Also detects -thinking suffix and returns a flag to enable thinking mode.

    Returns:
        tuple: (model_name, should_enable_thinking)
    """
    DEFAULT_MODEL = "claude-sonnet-4.5"

    # Available models in the service (with KIRO_CLI origin)
    # Based on actual testing: Sonnet 4.5, Haiku 4.5, and Opus 4.5 are supported
    VALID_MODELS = {"auto", "claude-sonnet-4.5", "claude-haiku-4.5", "claude-opus-4.5"}

    # Mapping from canonical names to short names
    CANONICAL_TO_SHORT = {
        # Sonnet 4 -> map to 4.5 (Sonnet 4 not supported by backend)
        "claude-sonnet-4-20250514": "claude-sonnet-4.5",
        # Sonnet 4.5
        "claude-sonnet-4-5-20250929": "claude-sonnet-4.5",
        # Opus 4.5
        "claude-opus-4-5-20251101": "claude-opus-4.5",
        # Legacy Claude 3.5 Sonnet models
        "claude-3-5-sonnet-20241022": "claude-sonnet-4.5",
        "claude-3-5-sonnet-20240620": "claude-sonnet-4.5",
        # Alternative hyphenated format
        "claude-sonnet-4-5": "claude-sonnet-4.5",
        "claude-opus-4-5": "claude-opus-4.5",
    }

    # Type safety and normalization
    if not isinstance(claude_model, str):
        logger.warning(f"Invalid model type {type(claude_model)}, falling back to default")
        return DEFAULT_MODEL, False

    # Normalize: strip whitespace, convert to lowercase, limit length
    model_normalized = claude_model.strip().lower()
    if len(model_normalized) > 100:
        logger.warning(f"Model name too long ({len(model_normalized)} chars), falling back to default")
        return DEFAULT_MODEL, False

    # Check for -thinking suffix and strip it
    thinking_requested = False
    if model_normalized.endswith("-thinking"):
        thinking_requested = True
        model_normalized = model_normalized[:-9]  # Remove "-thinking"
        logger.info(f"Detected -thinking suffix, will enable thinking mode for model: {model_normalized}")

    # Check if it's a valid short name (but not "auto" which Amazon Q doesn't accept)
    if model_normalized in VALID_MODELS and model_normalized != "auto":
        return model_normalized, thinking_requested

    # Check if it's a canonical name
    if model_normalized in CANONICAL_TO_SHORT:
        return CANONICAL_TO_SHORT[model_normalized], thinking_requested

    # Unknown model - log debug with truncated name and return default
    truncated_name = claude_model[:50] + "..." if len(claude_model) > 50 else claude_model
    logger.debug(f"Unknown model '{truncated_name}', falling back to default model '{DEFAULT_MODEL}'")
    return DEFAULT_MODEL, thinking_requested

def extract_text_from_content(content: Union[str, List[Dict[str, Any]]]) -> str:
    """Extract text from Claude content."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "thinking":
                    parts.append(_wrap_thinking_content(block.get("thinking", "")))
        return "\n".join(parts)
    return ""

def extract_images_from_content(content: Union[str, List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Extract images from Claude content and convert to Amazon Q format."""
    if not isinstance(content, list):
        return None
    
    images = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                media_type = source.get("media_type", "image/png")
                fmt = media_type.split("/")[-1] if "/" in media_type else "png"
                images.append({
                    "format": fmt,
                    "source": {
                        "bytes": source.get("data", "")
                    }
                })
    return images if images else None

def convert_tool(tool: ClaudeTool) -> Dict[str, Any]:
    """Convert Claude tool to Amazon Q tool."""
    # Check if this is a WebSearch tool
    if tool.is_web_search():
        # WebSearch tool format
        result = {
            "type": tool.type,
            "name": tool.name
        }
        if tool.max_uses is not None:
            result["max_uses"] = tool.max_uses
        return result

    # Regular tool format
    desc = tool.description or ""
    if len(desc) > 10240:
        desc = desc[:10100] + "\n\n...(Full description provided in TOOL DOCUMENTATION section)"

    return {
        "toolSpecification": {
            "name": tool.name,
            "description": desc,
            "inputSchema": {"json": tool.input_schema}
        }
    }

def merge_user_messages(messages: List[Dict[str, Any]], hint: str = THINKING_HINT) -> Dict[str, Any]:
    """Merge consecutive user messages, keeping only the last 2 messages' images.
    
    IMPORTANT: This function properly merges toolResults from all messages to prevent
    losing tool execution history, which would cause infinite loops.
    
    When merging messages that contain thinking hints, removes duplicate hints and 
    ensures only one hint appears at the end of the merged content.
    
    Args:
        messages: List of user messages to merge
        hint: The thinking hint string to deduplicate
    """
    if not messages:
        return {}
    
    all_contents = []
    base_context = None
    base_origin = None
    base_model = None
    all_images = []
    all_tool_results = []  # Collect toolResults from all messages
    
    for msg in messages:
        content = msg.get("content", "")
        msg_ctx = msg.get("userInputMessageContext", {})
        
        # Initialize base context from first message
        if base_context is None:
            base_context = msg_ctx.copy() if msg_ctx else {}
            # Remove toolResults from base to merge them separately
            if "toolResults" in base_context:
                all_tool_results.extend(base_context.pop("toolResults"))
        else:
            # Collect toolResults from subsequent messages
            if "toolResults" in msg_ctx:
                all_tool_results.extend(msg_ctx["toolResults"])
        
        if base_origin is None:
            base_origin = msg.get("origin", "KIRO_CLI")
        if base_model is None:
            base_model = msg.get("modelId")
        
        # Remove thinking hint from individual message content to avoid duplication
        # The hint will be added once at the end of the merged content
        if content:
            content_cleaned = content.replace(hint, "").strip()
            if content_cleaned:
                all_contents.append(content_cleaned)
        
        # Collect images from each message
        msg_images = msg.get("images")
        if msg_images:
            all_images.append(msg_images)
    
    # Merge content and ensure thinking hint appears only once at the end
    merged_content = "\n\n".join(all_contents)
    # Check if any of the original messages had the hint (indicating thinking was enabled)
    had_thinking_hint = any(hint in msg.get("content", "") for msg in messages)
    if had_thinking_hint:
        merged_content = _append_thinking_hint(merged_content, hint)
    
    result = {
        "content": merged_content,
        "userInputMessageContext": base_context or {},
        "origin": base_origin or "KIRO_CLI",
        "modelId": base_model
    }
    
    # Add merged toolResults if any
    if all_tool_results:
        result["userInputMessageContext"]["toolResults"] = all_tool_results
    
    # Only keep images from the last 2 messages that have images
    if all_images:
        kept_images = []
        for img_list in all_images[-2:]:  # Take last 2 messages' images
            kept_images.extend(img_list)
        if kept_images:
            result["images"] = kept_images
    
    return result

def _reorder_tool_results_by_tool_uses(tool_results: List[Dict[str, Any]], tool_use_order: List[str]) -> List[Dict[str, Any]]:
    """Reorder tool_results to match the order of tool_uses from the preceding assistant message.

    This is critical for preventing model confusion when parallel tool calls return results
    in a different order than they were called.

    Args:
        tool_results: List of tool_result dicts with toolUseId
        tool_use_order: List of toolUseIds in the order they appeared in the assistant message

    Returns:
        Reordered list of tool_results
    """
    if not tool_use_order or not tool_results:
        return tool_results

    result_by_id = {r.get("toolUseId"): r for r in tool_results if r.get("toolUseId")}
    results_without_id = [r for r in tool_results if not r.get("toolUseId")]
    ordered_results = []

    # Add results in the order of tool_uses
    for tool_use_id in tool_use_order:
        if tool_use_id in result_by_id:
            ordered_results.append(result_by_id.pop(tool_use_id))

    # Add any remaining results not in the original order (shouldn't happen normally)
    ordered_results.extend(result_by_id.values())
    ordered_results.extend(results_without_id)

    return ordered_results


def process_history(messages: List[ClaudeMessage], thinking_enabled: bool = False, hint: str = THINKING_HINT) -> List[Dict[str, Any]]:
    """Process history messages to match Amazon Q format (alternating user/assistant).
    
    Dual-mode detection:
    - If messages already alternate correctly (no consecutive user/assistant), skip merging
    - If messages have consecutive same-role messages, apply merge logic

    Key fix: Track tool_use order from assistant messages and reorder tool_results in user
    messages to match. This prevents model confusion when parallel tool calls return results
    in a different order than they were called.
    """
    history = []
    seen_tool_use_ids = set()  # Track tool_use IDs in assistant messages
    last_tool_use_order = []  # Track order of tool_uses from the last assistant message

    raw_history = []
    
    # First pass: convert individual messages
    for msg in messages:
        if msg.role == "user":
            content = msg.content
            text_content = ""
            tool_results = None
            images = extract_images_from_content(content)
            should_append_hint = thinking_enabled
            
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type")
                        if btype == "text":
                            text_parts.append(block.get("text", ""))
                        elif btype == "thinking":
                            text_parts.append(_wrap_thinking_content(block.get("thinking", "")))
                        elif btype == "tool_result":
                            if tool_results is None:
                                tool_results = []
                            
                            tool_use_id = block.get("tool_use_id")
                            raw_c = block.get("content", [])
                            
                            aq_content = []
                            if isinstance(raw_c, str):
                                aq_content = [{"text": raw_c}]
                            elif isinstance(raw_c, list):
                                for item in raw_c:
                                    if isinstance(item, dict):
                                        if item.get("type") == "text":
                                            aq_content.append({"text": item.get("text", "")})
                                        elif "text" in item:
                                            aq_content.append({"text": item["text"]})
                                    elif isinstance(item, str):
                                        aq_content.append({"text": item})
                            
                            # Check if there's actual content
                            if not any(i.get("text", "").strip() for i in aq_content):
                                # Use different message based on status
                                if block.get("status") != "error" and not block.get("is_error"):
                                    aq_content = [{"text": "Command executed successfully"}]
                                else:
                                    aq_content = [{"text": "Tool use was cancelled by the user"}]

                            # Determine status: check both 'status' field and 'is_error' flag
                            status = block.get("status")
                            if not status:
                                # If status not set, infer from is_error flag
                                status = "error" if block.get("is_error") else "success"

                            # Merge if exists
                            existing = next((r for r in tool_results if r["toolUseId"] == tool_use_id), None)
                            if existing:
                                existing["content"].extend(aq_content)
                                # Update status if this is an error
                                if status == "error":
                                    existing["status"] = "error"
                            else:
                                tool_results.append({
                                    "toolUseId": tool_use_id,
                                    "content": aq_content,
                                    "status": status
                                })
                text_content = "\n".join(text_parts)
            else:
                text_content = extract_text_from_content(content)

            if should_append_hint:
                text_content = _append_thinking_hint(text_content, hint)

            # Reorder tool_results to match the order of tool_uses from the preceding assistant message
            if tool_results and last_tool_use_order:
                tool_results = _reorder_tool_results_by_tool_uses(tool_results, last_tool_use_order)
                logger.info(f"Reordered {len(tool_results)} tool_results to match tool_uses order")

            user_ctx = {
                "envState": {
                    "operatingSystem": "macos",
                    "currentWorkingDirectory": "/"
                }
            }
            if tool_results:
                user_ctx["toolResults"] = tool_results
                
            u_msg = {
                "content": text_content,
                "userInputMessageContext": user_ctx,
                "origin": "KIRO_CLI"
            }
            if images:
                u_msg["images"] = images

            raw_history.append({"userInputMessage": u_msg})

        elif msg.role == "assistant":
            content = msg.content
            text_content = extract_text_from_content(content)

            entry = {
                "assistantResponseMessage": {
                    "messageId": str(uuid.uuid4()),
                    "content": text_content
                }
            }

            # Track tool_use order for reordering tool_results in the next user message
            last_tool_use_order = []
            if isinstance(content, list):
                tool_uses = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tid = block.get("id")
                        if tid and tid not in seen_tool_use_ids:
                            seen_tool_use_ids.add(tid)
                            last_tool_use_order.append(tid)  # Track order
                            tool_uses.append({
                                "toolUseId": tid,
                                "name": block.get("name"),
                                "input": block.get("input", {})
                            })
                if tool_uses:
                    entry["assistantResponseMessage"]["toolUses"] = tool_uses

            raw_history.append(entry)

    # Dual-mode detection: check if messages already alternate correctly
    has_consecutive_same_role = False
    prev_role = None
    for item in raw_history:
        current_role = "user" if "userInputMessage" in item else "assistant"
        if prev_role == current_role:
            has_consecutive_same_role = True
            break
        prev_role = current_role
    
    # If messages already alternate, skip merging (fast path)
    if not has_consecutive_same_role:
        logger.info("Messages already alternate correctly, skipping merge logic")
        return raw_history

    # Second pass: merge consecutive user messages (only if needed)
    logger.info("Detected consecutive same-role messages, applying merge logic")
    pending_user_msgs = []
    for item in raw_history:
        if "userInputMessage" in item:
            user_msg = item["userInputMessage"]
            has_tool_results = bool(
                user_msg.get("userInputMessageContext", {}).get("toolResults")
            )
            if has_tool_results:
                if pending_user_msgs:
                    merged = merge_user_messages(pending_user_msgs, hint)
                    history.append({"userInputMessage": merged})
                    pending_user_msgs = []
                history.append(item)
            else:
                pending_user_msgs.append(user_msg)
        elif "assistantResponseMessage" in item:
            if pending_user_msgs:
                merged = merge_user_messages(pending_user_msgs, hint)
                history.append({"userInputMessage": merged})
                pending_user_msgs = []
            history.append(item)
            
    if pending_user_msgs:
        merged = merge_user_messages(pending_user_msgs, hint)
        history.append({"userInputMessage": merged})
        
    return history

def _validate_history_alternation(history: List[Dict[str, Any]]) -> None:
    """Validate that history messages alternate correctly (user-assistant-user-assistant...)."""
    if not history:
        return

    prev_role = None
    for idx, item in enumerate(history):
        if "userInputMessage" in item:
            current_role = "user"
        elif "assistantResponseMessage" in item:
            current_role = "assistant"
        else:
            continue

        if prev_role == current_role:
            raise ValueError(
                f"Message {idx} violates alternation rule: consecutive {current_role} messages. "
                f"This may indicate malformed message ordering that could cause infinite loops."
            )
        prev_role = current_role


def _validate_tool_pairing(messages: List[ClaudeMessage], tools: Optional[List[Any]] = None) -> tuple[set, set, List[Dict[str, Any]]]:
    """Validate tool_use/tool_result pairing and detect orphans.

    Returns:
        tuple: (orphan_tool_use_ids, orphan_tool_result_ids, placeholder_tools)
            - orphan_tool_use_ids: tool_use IDs without matching tool_result
            - orphan_tool_result_ids: tool_result IDs without matching tool_use
            - placeholder_tools: auto-generated tool definitions for missing tools
    """
    tool_use_ids = set()
    tool_use_names = {}  # id -> name mapping
    tool_result_ids = set()
    defined_tool_names = set()

    # Collect defined tool names
    if tools:
        for t in tools:
            defined_tool_names.add(t.name)

    # First pass: collect all tool_use IDs and names from assistant messages
    for msg in messages:
        if msg.role == "assistant" and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tid = block.get("id")
                    tname = block.get("name")
                    if tid:
                        tool_use_ids.add(tid)
                        if tname:
                            tool_use_names[tid] = tname

    # Second pass: collect all tool_result IDs from user messages
    for msg in messages:
        if msg.role == "user" and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid:
                        tool_result_ids.add(tid)

    # Find orphans
    orphan_tool_use_ids = tool_use_ids - tool_result_ids
    orphan_tool_result_ids = tool_result_ids - tool_use_ids

    if orphan_tool_use_ids:
        logger.warning(f"Orphan tool_use without results: {orphan_tool_use_ids}")
    if orphan_tool_result_ids:
        logger.warning(f"Orphan tool_result without uses: {orphan_tool_result_ids}")

    # Generate placeholder tools for tool_uses that reference undefined tools
    placeholder_tools = []
    used_tool_names = set(tool_use_names.values())
    missing_tool_names = used_tool_names - defined_tool_names

    for name in missing_tool_names:
        logger.info(f"Generating placeholder tool definition for: {name}")
        placeholder_tools.append({
            "toolSpecification": {
                "name": name,
                "description": f"[Auto-generated placeholder] Tool '{name}' was used in conversation history but not defined in current request.",
                "inputSchema": {"json": {"type": "object", "properties": {}}}
            }
        })

    return orphan_tool_use_ids, orphan_tool_result_ids, placeholder_tools


def _detect_tool_call_loop(messages: List[ClaudeMessage], threshold: int = 3) -> Optional[str]:
    """Detect if the same tool is being called repeatedly (potential infinite loop).

    Only triggers if:
    1. Same tool called N times with same input
    2. All calls are in CONSECUTIVE assistant messages (no user messages between them)
    """
    recent_tool_calls = []
    consecutive_count = 0
    last_tool_call = None

    for msg in messages[-10:]:  # Check last 10 messages
        if msg.role == "assistant" and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_name = block.get("name")
                    tool_input = json.dumps(block.get("input", {}), sort_keys=True)
                    current_call = (tool_name, tool_input)

                    if current_call == last_tool_call:
                        consecutive_count += 1
                    else:
                        consecutive_count = 1
                        last_tool_call = current_call

                    recent_tool_calls.append(current_call)
        elif msg.role == "user":
            # User message breaks the consecutive chain
            consecutive_count = 0
            last_tool_call = None

    # Only trigger if we have consecutive identical calls
    if consecutive_count >= threshold:
        return f"Detected infinite loop: tool '{last_tool_call[0]}' called {consecutive_count} times consecutively with same input"

    return None

def convert_claude_to_amazonq_request(req: ClaudeRequest, conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """Convert ClaudeRequest to Amazon Q request body."""
    if conversation_id is None:
        conversation_id = str(uuid.uuid4())

    # Detect infinite tool call loops
    loop_error = _detect_tool_call_loop(req.messages, threshold=3)
    if loop_error:
        raise ValueError(loop_error)

    # Check for thinking mode: either from explicit thinking parameter or -thinking model suffix
    thinking_enabled = is_thinking_mode_enabled(getattr(req, "thinking", None))

    # 5. Model - check if model name includes -thinking suffix
    model_id, model_requests_thinking = map_model_name(req.model)

    # Enable thinking if either the parameter or model suffix requests it
    if model_requests_thinking and not thinking_enabled:
        thinking_enabled = True
        logger.info("Enabling thinking mode based on -thinking model suffix")

    # Validate tool pairing and generate placeholders for missing tools
    _, _, placeholder_tools = _validate_tool_pairing(req.messages, req.tools)

    aq_tools = []
    long_desc_tools = []
    if req.tools:
        for t in req.tools:
            if t.description and len(t.description) > 10240:
                long_desc_tools.append({"name": t.name, "full_description": t.description})
            aq_tools.append(convert_tool(t))

    # Add placeholder tools for undefined tools used in history
    aq_tools.extend(placeholder_tools)
            
    # 2. Current Message (last user message)
    last_msg = req.messages[-1] if req.messages else None
    prompt_content = ""
    tool_results = None
    has_tool_result = False
    images = None
    
    if last_msg and last_msg.role == "user":
        content = last_msg.content
        images = extract_images_from_content(content)
        
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "thinking":
                        text_parts.append(_wrap_thinking_content(block.get("thinking", "")))
                    elif btype == "tool_result":
                        has_tool_result = True
                        if tool_results is None:
                            tool_results = []
                        
                        tid = block.get("tool_use_id")
                        raw_c = block.get("content", [])
                        
                        aq_content = []
                        if isinstance(raw_c, str):
                            aq_content = [{"text": raw_c}]
                        elif isinstance(raw_c, list):
                            for item in raw_c:
                                if isinstance(item, dict):
                                    if item.get("type") == "text":
                                        aq_content.append({"text": item.get("text", "")})
                                    elif "text" in item:
                                        aq_content.append({"text": item["text"]})
                                elif isinstance(item, str):
                                    aq_content.append({"text": item})
                                    
                        # Check if there's actual content
                        if not any(i.get("text", "").strip() for i in aq_content):
                            # Use different message based on status
                            if block.get("status") != "error" and not block.get("is_error"):
                                aq_content = [{"text": "Command executed successfully"}]
                            else:
                                aq_content = [{"text": "Tool use was cancelled by the user"}]

                        # Determine status: check both 'status' field and 'is_error' flag
                        status = block.get("status")
                        if not status:
                            # If status not set, infer from is_error flag
                            status = "error" if block.get("is_error") else "success"

                        existing = next((r for r in tool_results if r["toolUseId"] == tid), None)
                        if existing:
                            existing["content"].extend(aq_content)
                            # Update status if this is an error
                            if status == "error":
                                existing["status"] = "error"
                        else:
                            tool_results.append({
                                "toolUseId": tid,
                                "content": aq_content,
                                "status": status
                            })
            prompt_content = "\n".join(text_parts)
        else:
            prompt_content = extract_text_from_content(content)

    # Get tool_use order from the last assistant message for reordering current message's tool_results
    last_tool_use_order = []
    if len(req.messages) >= 2:
        # Find the last assistant message before the current user message
        for i in range(len(req.messages) - 2, -1, -1):
            if req.messages[i].role == "assistant":
                assistant_content = req.messages[i].content
                if isinstance(assistant_content, list):
                    for block in assistant_content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tid = block.get("id")
                            if tid:
                                last_tool_use_order.append(tid)
                break

    # Reorder tool_results to match the order of tool_uses from the preceding assistant message
    if tool_results and last_tool_use_order:
        tool_results = _reorder_tool_results_by_tool_uses(tool_results, last_tool_use_order)
        logger.info(f"Reordered {len(tool_results)} current message tool_results to match tool_uses order")

    # 3. Context
    user_ctx = {
        "envState": {
            "operatingSystem": "macos",
            "currentWorkingDirectory": "/"
        }
    }
    if aq_tools:
        user_ctx["tools"] = aq_tools
    if tool_results:
        user_ctx["toolResults"] = tool_results
        
    # 4. Format Content
    formatted_content = ""
    if has_tool_result and not prompt_content:
        formatted_content = ""
    else:
        formatted_content = (
            "--- CONTEXT ENTRY BEGIN ---\n"
            f"Current time: {get_current_timestamp()}\n"
            "--- CONTEXT ENTRY END ---\n\n"
            "--- USER MESSAGE BEGIN ---\n"
            f"{prompt_content}\n"
            "--- USER MESSAGE END ---"
        )
        
    if long_desc_tools:
        docs = []
        for info in long_desc_tools:
            docs.append(f"Tool: {info['name']}\nFull Description:\n{info['full_description']}\n")
        formatted_content = (
            "--- TOOL DOCUMENTATION BEGIN ---\n"
            f"{''.join(docs)}"
            "--- TOOL DOCUMENTATION END ---\n\n"
            f"{formatted_content}"
        )
        
    if req.system and formatted_content:
        sys_text = ""
        if isinstance(req.system, str):
            sys_text = req.system
        elif isinstance(req.system, list):
            parts = []
            for b in req.system:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
            sys_text = "\n".join(parts)
            
        if sys_text:
            formatted_content = (
                "--- SYSTEM PROMPT BEGIN ---\n"
                f"{sys_text}\n"
                "--- SYSTEM PROMPT END ---\n\n"
                f"{formatted_content}"
            )

    # Append thinking hint at the very end, outside all structured blocks
    if thinking_enabled:
        formatted_content = _append_thinking_hint(formatted_content)

    # Model ID was already determined above with -thinking detection
    # Sanitize model name for logging: truncate and escape control characters
    if isinstance(req.model, str):
        model_str = req.model
        safe_model_name = model_str[:50].replace('\n', '\\n').replace('\r', '\\r')
        if len(model_str) > 50:
            safe_model_name += "..."
    else:
        model_str = str(req.model)
        safe_model_name = model_str[:50]
        if len(model_str) > 50:
            safe_model_name += "..."
    logger.info(f"Model mapping: '{safe_model_name}' -> '{model_id}'")

    # 6. User Input Message
    user_input_msg = {
        "content": formatted_content,
        "userInputMessageContext": user_ctx,
        "origin": "KIRO_CLI",
        "modelId": model_id
    }
    if images:
        user_input_msg["images"] = images
        
    # 7. History
    history_msgs = req.messages[:-1] if len(req.messages) > 1 else []
    aq_history = process_history(history_msgs, thinking_enabled=thinking_enabled, hint=THINKING_HINT)

    # Validate history alternation to prevent infinite loops
    _validate_history_alternation(aq_history)

    # 8. Final Body
    return {
        "conversationState": {
            "conversationId": conversation_id,
            "history": aq_history,
            "currentMessage": {
                "userInputMessage": user_input_msg
            },
            "chatTriggerType": "MANUAL"
        }
    }
