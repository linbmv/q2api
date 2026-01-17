import json
import logging
import importlib.util
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional, Dict, Any, List, Set

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Tokenizer (optional - fallback to estimation if tiktoken unavailable)
# ------------------------------------------------------------------------------

try:
    import tiktoken
    ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:
    tiktoken = None
    ENCODING = None

THINKING_START_TAG = "<thinking>"
THINKING_END_TAG = "</thinking>"

def _pending_tag_suffix(buffer: str, tag: str) -> int:
    """Length of the suffix of buffer that matches the prefix of tag (for partial matches)."""
    if not buffer or not tag:
        return 0
    max_len = min(len(buffer), len(tag) - 1)
    for length in range(max_len, 0, -1):
        if buffer[-length:] == tag[:length]:
            return length
    return 0

def count_tokens(text: str) -> int:
    """Counts tokens with tiktoken."""
    if not text or not ENCODING:
        return 0
    return len(ENCODING.encode(text))

# ------------------------------------------------------------------------------
# Smart Tag Detection (ported from kiro.rs)
# ------------------------------------------------------------------------------

class QuoteState:
    """Persistent quote state tracker for cross-chunk detection."""
    __slots__ = ('in_single', 'in_double', 'in_backtick', 'in_triple_backtick')

    def __init__(self):
        self.in_single = False
        self.in_double = False
        self.in_backtick = False
        self.in_triple_backtick = False

    def is_inside_quotes(self) -> bool:
        return self.in_single or self.in_double or self.in_backtick or self.in_triple_backtick

    def update(self, text: str) -> None:
        """Update quote state by scanning text."""
        i = 0
        while i < len(text):
            if i + 2 < len(text) and text[i:i+3] == '```':
                self.in_triple_backtick = not self.in_triple_backtick
                i += 3
                continue
            ch = text[i]
            if ch == '\\' and i + 1 < len(text):
                next_ch = text[i + 1]
                if next_ch in ('"', "'", '`', '\\'):
                    i += 2
                    continue
            if ch == '`' and not self.in_triple_backtick and not self.in_single and not self.in_double:
                self.in_backtick = not self.in_backtick
            elif ch == '"' and not self.in_single and not self.in_backtick and not self.in_triple_backtick:
                self.in_double = not self.in_double
            elif ch == "'" and not self.in_double and not self.in_backtick and not self.in_triple_backtick:
                self.in_single = not self.in_single
            i += 1

    def check_at_position(self, text: str, pos: int) -> bool:
        """Check if position in text is inside quotes (stateless check from start)."""
        in_single = self.in_single
        in_double = self.in_double
        in_backtick = self.in_backtick
        in_triple_backtick = self.in_triple_backtick
        i = 0
        while i < pos:
            if i + 2 < len(text) and text[i:i+3] == '```':
                in_triple_backtick = not in_triple_backtick
                i += 3
                continue
            ch = text[i]
            if ch == '\\' and i + 1 < len(text):
                next_ch = text[i + 1]
                if next_ch in ('"', "'", '`', '\\'):
                    i += 2
                    continue
            if ch == '`' and not in_triple_backtick and not in_single and not in_double:
                in_backtick = not in_backtick
            elif ch == '"' and not in_single and not in_backtick and not in_triple_backtick:
                in_double = not in_double
            elif ch == "'" and not in_double and not in_backtick and not in_triple_backtick:
                in_single = not in_single
            i += 1
        return in_single or in_double or in_backtick or in_triple_backtick

    def reset(self) -> None:
        self.in_single = False
        self.in_double = False
        self.in_backtick = False
        self.in_triple_backtick = False

def _is_inside_quotes(text: str, pos: int) -> bool:
    """Check if position is inside single/double quotes or backticks (stateless)."""
    state = QuoteState()
    return state.check_at_position(text, pos)

def find_real_tag(text: str, tag: str, start: int = 0, quote_state: QuoteState = None) -> int:
    """Find tag that is not inside quotes/backticks."""
    pos = start
    while True:
        idx = text.find(tag, pos)
        if idx == -1:
            return -1
        if quote_state:
            if not quote_state.check_at_position(text, idx):
                return idx
        elif not _is_inside_quotes(text, idx):
            return idx
        pos = idx + 1
    return -1

# ------------------------------------------------------------------------------
# Dynamic Loader
# ------------------------------------------------------------------------------

def _load_claude_parser():
    """Dynamically load claude_parser module."""
    base_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("v2_claude_parser", str(base_dir / "claude_parser.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

try:
    _parser = _load_claude_parser()
    build_message_start = _parser.build_message_start
    build_content_block_start = _parser.build_content_block_start
    build_content_block_delta = _parser.build_content_block_delta
    build_content_block_stop = _parser.build_content_block_stop
    build_ping = _parser.build_ping
    build_message_stop = _parser.build_message_stop
    build_tool_use_start = _parser.build_tool_use_start
    build_tool_use_input_delta = _parser.build_tool_use_input_delta
except Exception as e:
    logger.error(f"Failed to load claude_parser: {e}")
    # Fallback definitions
    def build_message_start(*args, **kwargs): return ""
    def build_content_block_start(*args, **kwargs): return ""
    def build_content_block_delta(*args, **kwargs): return ""
    def build_content_block_stop(*args, **kwargs): return ""
    def build_ping(*args, **kwargs): return ""
    def build_message_stop(*args, **kwargs): return ""
    def build_tool_use_start(*args, **kwargs): return ""
    def build_tool_use_input_delta(*args, **kwargs): return ""

class ClaudeStreamHandler:
    def __init__(self, model: str, input_tokens: int = 0, conversation_id: Optional[str] = None):
        self.model = model
        self.input_tokens = input_tokens
        self.response_buffer: List[str] = []
        self.content_block_index: int = -1
        self.content_block_started: bool = False
        self.content_block_start_sent: bool = False
        self.content_block_stop_sent: bool = False
        self.message_start_sent: bool = False
        self.conversation_id: Optional[str] = conversation_id

        # Tool use state
        self.current_tool_use: Optional[Dict[str, Any]] = None
        self.tool_input_buffer: List[str] = []
        self.tool_use_id: Optional[str] = None
        self.tool_name: Optional[str] = None
        self._processed_tool_use_ids: Set[str] = set()
        self.all_tool_inputs: List[str] = []
        self.has_tool_use: bool = False  # Track if any tool_use was emitted
        self.response_ended: bool = False  # Track if response has ended

        # Think tag state
        self.in_think_block: bool = False
        self.think_buffer: str = ""
        self.pending_start_tag_chars: int = 0
        self.quote_state: QuoteState = QuoteState()  # Persistent quote state

    async def handle_event(self, event_type: str, payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Process a single Amazon Q event and yield Claude SSE events."""

        # Early return if response has already ended
        if self.response_ended:
            return

        # 1. Message Start (initial-response)
        if event_type == "initial-response":
            if not self.message_start_sent:
                # Use conversation_id from payload if available, otherwise use the one passed to constructor
                conv_id = payload.get('conversationId') or self.conversation_id or str(uuid.uuid4())
                self.conversation_id = conv_id
                yield build_message_start(conv_id, self.model, self.input_tokens)
                self.message_start_sent = True
                yield build_ping()

        # 2. Content Block Delta (assistantResponseEvent)
        elif event_type == "assistantResponseEvent":
            content = payload.get("content", "")

            # Close any open tool use block
            if self.current_tool_use and not self.content_block_stop_sent:
                yield build_content_block_stop(self.content_block_index)
                self.content_block_stop_sent = True
                self.current_tool_use = None

            # Process content with think tag detection
            if content:
                self.think_buffer += content
                while self.think_buffer:
                    if self.pending_start_tag_chars > 0:
                        if len(self.think_buffer) < self.pending_start_tag_chars:
                            self.pending_start_tag_chars -= len(self.think_buffer)
                            self.think_buffer = ""
                            break
                        else:
                            self.think_buffer = self.think_buffer[self.pending_start_tag_chars:]
                            self.pending_start_tag_chars = 0
                            if not self.think_buffer:
                                break
                            continue

                    if not self.in_think_block:
                        think_start = find_real_tag(self.think_buffer, THINKING_START_TAG, 0, self.quote_state)
                        if think_start == -1:
                            pending = _pending_tag_suffix(self.think_buffer, THINKING_START_TAG)
                            if pending == len(self.think_buffer) and pending > 0:
                                if self.content_block_start_sent:
                                    yield build_content_block_stop(self.content_block_index)
                                    self.content_block_stop_sent = True
                                    self.content_block_start_sent = False

                                self.content_block_index += 1
                                yield build_content_block_start(self.content_block_index, "thinking")
                                self.content_block_start_sent = True
                                self.content_block_started = True
                                self.content_block_stop_sent = False
                                self.in_think_block = True
                                self.pending_start_tag_chars = len(THINKING_START_TAG) - pending
                                self.think_buffer = ""
                                break

                            emit_len = len(self.think_buffer) - pending
                            if emit_len <= 0:
                                break
                            text_chunk = self.think_buffer[:emit_len]
                            if text_chunk:
                                if not self.content_block_start_sent:
                                    self.content_block_index += 1
                                    yield build_content_block_start(self.content_block_index, "text")
                                    self.content_block_start_sent = True
                                    self.content_block_started = True
                                    self.content_block_stop_sent = False
                                self.response_buffer.append(text_chunk)
                                yield build_content_block_delta(self.content_block_index, text_chunk)
                                self.quote_state.update(text_chunk)  # Update quote state after emitting
                            self.think_buffer = self.think_buffer[emit_len:]
                        else:
                            before_text = self.think_buffer[:think_start]
                            if before_text:
                                if not self.content_block_start_sent:
                                    self.content_block_index += 1
                                    yield build_content_block_start(self.content_block_index, "text")
                                    self.content_block_start_sent = True
                                    self.content_block_started = True
                                    self.content_block_stop_sent = False
                                self.response_buffer.append(before_text)
                                yield build_content_block_delta(self.content_block_index, before_text)
                                self.quote_state.update(before_text)  # Update quote state
                            self.think_buffer = self.think_buffer[think_start + len(THINKING_START_TAG):]
                            self.quote_state.reset()  # Reset quote state when entering thinking block

                            if self.content_block_start_sent:
                                yield build_content_block_stop(self.content_block_index)
                                self.content_block_stop_sent = True
                                self.content_block_start_sent = False

                            self.content_block_index += 1
                            yield build_content_block_start(self.content_block_index, "thinking")
                            self.content_block_start_sent = True
                            self.content_block_started = True
                            self.content_block_stop_sent = False
                            self.in_think_block = True
                            self.pending_start_tag_chars = 0
                    else:
                        think_end = find_real_tag(self.think_buffer, THINKING_END_TAG, 0, self.quote_state)
                        if think_end == -1:
                            pending = _pending_tag_suffix(self.think_buffer, THINKING_END_TAG)
                            emit_len = len(self.think_buffer) - pending
                            if emit_len <= 0:
                                break
                            thinking_chunk = self.think_buffer[:emit_len]
                            if thinking_chunk:
                                yield build_content_block_delta(
                                    self.content_block_index,
                                    thinking_chunk,
                                    delta_type="thinking_delta",
                                    field_name="thinking"
                                )
                            self.think_buffer = self.think_buffer[emit_len:]
                        else:
                            thinking_chunk = self.think_buffer[:think_end]
                            if thinking_chunk:
                                yield build_content_block_delta(
                                    self.content_block_index,
                                    thinking_chunk,
                                    delta_type="thinking_delta",
                                    field_name="thinking"
                                )
                            self.think_buffer = self.think_buffer[think_end + len(THINKING_END_TAG):]

                            yield build_content_block_stop(self.content_block_index)
                            self.content_block_stop_sent = True
                            self.content_block_start_sent = False
                            self.in_think_block = False

        # 3. Tool Use (toolUseEvent)
        elif event_type == "toolUseEvent":
            tool_use_id = payload.get("toolUseId")
            tool_name = payload.get("name")
            tool_input = payload.get("input", {})
            is_stop = payload.get("stop", False)

            # Start new tool use
            if tool_use_id and tool_name and not self.current_tool_use:
                # Close previous text block if open
                if self.content_block_start_sent and not self.content_block_stop_sent:
                    yield build_content_block_stop(self.content_block_index)
                    self.content_block_stop_sent = True

                self._processed_tool_use_ids.add(tool_use_id)
                self.content_block_index += 1
                
                yield build_tool_use_start(self.content_block_index, tool_use_id, tool_name)
                
                self.content_block_started = True
                self.current_tool_use = {"toolUseId": tool_use_id, "name": tool_name}
                self.tool_use_id = tool_use_id
                self.tool_name = tool_name
                self.tool_input_buffer = []
                self.content_block_stop_sent = False
                self.content_block_start_sent = True
                self.has_tool_use = True  # Mark that we have tool_use

            # Accumulate input
            if self.current_tool_use and tool_input:
                fragment = ""
                if isinstance(tool_input, str):
                    fragment = tool_input
                else:
                    fragment = json.dumps(tool_input, ensure_ascii=False)
                
                self.tool_input_buffer.append(fragment)
                yield build_tool_use_input_delta(self.content_block_index, fragment)

            # Stop tool use
            if is_stop and self.current_tool_use:
                full_input = "".join(self.tool_input_buffer)
                self.all_tool_inputs.append(full_input)
                
                yield build_content_block_stop(self.content_block_index)
                self.content_block_stop_sent = True
                self.content_block_started = False
                self.current_tool_use = None
                self.tool_use_id = None
                self.tool_name = None
                self.tool_input_buffer = []

        # 4. Assistant Response End (assistantResponseEnd)
        elif event_type == "assistantResponseEnd":
            # Close any open block
            if self.content_block_started and not self.content_block_stop_sent:
                yield build_content_block_stop(self.content_block_index)
                self.content_block_stop_sent = True

            # Mark as finished to prevent processing further events
            self.response_ended = True

            # Immediately send message_stop (instead of waiting for finish())
            full_text = "".join(self.response_buffer)
            full_tool_input = "".join(self.all_tool_inputs)
            output_tokens = count_tokens(full_text) + count_tokens(full_tool_input)
            # Use "tool_use" stop_reason if any tool_use was emitted, otherwise "end_turn"
            stop_reason = "tool_use" if self.has_tool_use else "end_turn"
            yield build_message_stop(self.input_tokens, output_tokens, stop_reason)

    async def finish(self) -> AsyncGenerator[str, None]:
        """Send final events."""
        # Skip if response already ended (message_stop was sent in handle_event)
        if self.response_ended:
            return

        # Ensure last block is closed
        if self.content_block_started and not self.content_block_stop_sent:
            yield build_content_block_stop(self.content_block_index)
            self.content_block_stop_sent = True

        # Calculate output tokens (approximate)
        full_text = "".join(self.response_buffer)
        full_tool_input = "".join(self.all_tool_inputs)
        # Simple approximation: 4 chars per token
        # output_tokens = max(1, (len(full_text) + len(full_tool_input)) // 4)
        output_tokens = count_tokens(full_text) + count_tokens(full_tool_input)

        # Use "tool_use" stop_reason if any tool_use was emitted, otherwise "end_turn"
        stop_reason = "tool_use" if self.has_tool_use else "end_turn"
        yield build_message_stop(self.input_tokens, output_tokens, stop_reason)
