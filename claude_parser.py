import json
import struct
import logging
from typing import Optional, Dict, Any, AsyncIterator
from enum import Enum, auto

logger = logging.getLogger(__name__)

# Try to import Rust extension for hardware-accelerated CRC32C
_USE_RUST_EXTENSION = False
try:
    import q2api_core as _rust
    _USE_RUST_EXTENSION = True
    logger.info("Using Rust extension for CRC32C acceleration")
except ImportError:
    logger.debug("Rust extension not available, using pure Python fallback")

# ------------------------------------------------------------------------------
# CRC32C Implementation (Castagnoli polynomial, used by AWS Event Stream)
# ------------------------------------------------------------------------------

CRC32C_TABLE = None

def _init_crc32c_table():
    """Initialize CRC32C lookup table with Castagnoli polynomial."""
    global CRC32C_TABLE
    if CRC32C_TABLE is not None:
        return
    poly = 0x82F63B78
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
        table.append(crc)
    CRC32C_TABLE = table

def _crc32c_python(data: bytes, initial: int = 0) -> int:
    """Pure Python CRC32C implementation."""
    _init_crc32c_table()
    crc = initial ^ 0xFFFFFFFF
    for byte in data:
        crc = CRC32C_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF

def crc32c(data: bytes, initial: int = 0) -> int:
    """Calculate CRC32C checksum (uses Rust extension if available)."""
    if _USE_RUST_EXTENSION:
        return _rust.compute_crc32c(data)
    return _crc32c_python(data, initial)

# ------------------------------------------------------------------------------
# Decoder State Machine
# ------------------------------------------------------------------------------

class DecoderState(Enum):
    READY = auto()
    PARSING = auto()
    RECOVERING = auto()
    STOPPED = auto()

class EventStreamDecoder:
    """State machine decoder with error recovery for AWS Event Stream."""

    def __init__(self, max_errors: int = 3, validate_crc: bool = True):
        self.state = DecoderState.READY
        self.buffer = bytearray()
        self.error_count = 0
        self.max_errors = max_errors
        self.validate_crc = validate_crc
        self.messages_parsed = 0
        self.crc_errors = 0

    def feed(self, data: bytes) -> list:
        """Feed data and return parsed messages."""
        if self.state == DecoderState.STOPPED:
            return []

        self.buffer.extend(data)
        messages = []

        while True:
            if self.state == DecoderState.RECOVERING:
                if not self._try_recover():
                    break
                self.state = DecoderState.READY

            if len(self.buffer) < 12:
                break

            self.state = DecoderState.PARSING
            result = self._try_parse_message()

            if result is None:
                break
            elif result is False:
                self.error_count += 1
                if self.error_count >= self.max_errors:
                    self.state = DecoderState.STOPPED
                    logger.error(f"Max errors ({self.max_errors}) reached, decoder stopped")
                    break
                self.state = DecoderState.RECOVERING
            else:
                self.state = DecoderState.READY
                self.error_count = 0
                self.messages_parsed += 1
                messages.append(result)

        return messages

    def _try_parse_message(self) -> Optional[Dict[str, Any]]:
        """Try to parse a message. Returns message dict, None (need more data), or False (error)."""
        try:
            total_length = struct.unpack('>I', self.buffer[0:4])[0]

            if total_length < 16 or total_length > 16 * 1024 * 1024:
                logger.warning(f"Invalid message length: {total_length}")
                return False

            if len(self.buffer) < total_length:
                return None

            message_data = bytes(self.buffer[:total_length])

            if self.validate_crc:
                prelude_crc_expected = struct.unpack('>I', message_data[8:12])[0]
                prelude_crc_actual = crc32c(message_data[0:8])
                if prelude_crc_expected != prelude_crc_actual:
                    logger.warning(f"Prelude CRC mismatch: expected {prelude_crc_expected:#x}, got {prelude_crc_actual:#x}")
                    self.crc_errors += 1
                    return False

                message_crc_expected = struct.unpack('>I', message_data[-4:])[0]
                message_crc_actual = crc32c(message_data[:-4])
                if message_crc_expected != message_crc_actual:
                    logger.warning(f"Message CRC mismatch: expected {message_crc_expected:#x}, got {message_crc_actual:#x}")
                    self.crc_errors += 1
                    return False

            headers_length = struct.unpack('>I', message_data[4:8])[0]
            headers_data = message_data[12:12 + headers_length]
            headers = EventStreamParser.parse_headers(headers_data)

            payload_start = 12 + headers_length
            payload_end = total_length - 4
            payload_data = message_data[payload_start:payload_end]

            payload = None
            if payload_data:
                try:
                    payload = json.loads(payload_data.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = payload_data

            del self.buffer[:total_length]

            return {
                'headers': headers,
                'payload': payload,
                'total_length': total_length
            }

        except struct.error as e:
            logger.warning(f"Struct parsing error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected parse error: {e}")
            return False

    def _try_recover(self) -> bool:
        """Try to recover from parse error by scanning for next valid message."""
        if len(self.buffer) < 12:
            return False

        del self.buffer[0]

        for i in range(len(self.buffer) - 11):
            try:
                total_length = struct.unpack('>I', self.buffer[i:i+4])[0]
                if 16 <= total_length <= 16 * 1024 * 1024:
                    if len(self.buffer) >= i + 12:
                        prelude = self.buffer[i:i+8]
                        prelude_crc = struct.unpack('>I', self.buffer[i+8:i+12])[0]
                        if crc32c(bytes(prelude)) == prelude_crc:
                            del self.buffer[:i]
                            logger.info(f"Recovered at offset {i}")
                            return True
            except struct.error:
                continue

        if len(self.buffer) > 16 * 1024:
            del self.buffer[:len(self.buffer) - 1024]

        return False

    def reset(self):
        """Reset decoder state."""
        self.state = DecoderState.READY
        self.buffer.clear()
        self.error_count = 0

class EventStreamParser:
    """AWS Event Stream binary format parser (v2 style) with CRC32C validation."""

    # Header value types per AWS Event Stream spec
    HEADER_TYPE_BOOL_TRUE = 0
    HEADER_TYPE_BOOL_FALSE = 1
    HEADER_TYPE_BYTE = 2
    HEADER_TYPE_SHORT = 3
    HEADER_TYPE_INT = 4
    HEADER_TYPE_LONG = 5
    HEADER_TYPE_BYTES = 6
    HEADER_TYPE_STRING = 7
    HEADER_TYPE_TIMESTAMP = 8
    HEADER_TYPE_UUID = 9

    @staticmethod
    def parse_headers(headers_data: bytes) -> Dict[str, Any]:
        """Parse event stream headers with full type support."""
        headers = {}
        offset = 0

        while offset < len(headers_data):
            if offset >= len(headers_data):
                break
            name_length = headers_data[offset]
            offset += 1

            if offset + name_length > len(headers_data):
                break
            name = headers_data[offset:offset + name_length].decode('utf-8')
            offset += name_length

            if offset >= len(headers_data):
                break
            value_type = headers_data[offset]
            offset += 1

            value = None

            if value_type == EventStreamParser.HEADER_TYPE_BOOL_TRUE:
                value = True
            elif value_type == EventStreamParser.HEADER_TYPE_BOOL_FALSE:
                value = False
            elif value_type == EventStreamParser.HEADER_TYPE_BYTE:
                if offset + 1 > len(headers_data):
                    break
                value = headers_data[offset]
                offset += 1
            elif value_type == EventStreamParser.HEADER_TYPE_SHORT:
                if offset + 2 > len(headers_data):
                    break
                value = struct.unpack('>h', headers_data[offset:offset + 2])[0]
                offset += 2
            elif value_type == EventStreamParser.HEADER_TYPE_INT:
                if offset + 4 > len(headers_data):
                    break
                value = struct.unpack('>i', headers_data[offset:offset + 4])[0]
                offset += 4
            elif value_type == EventStreamParser.HEADER_TYPE_LONG:
                if offset + 8 > len(headers_data):
                    break
                value = struct.unpack('>q', headers_data[offset:offset + 8])[0]
                offset += 8
            elif value_type == EventStreamParser.HEADER_TYPE_TIMESTAMP:
                if offset + 8 > len(headers_data):
                    break
                value = struct.unpack('>q', headers_data[offset:offset + 8])[0]
                offset += 8
            elif value_type in (EventStreamParser.HEADER_TYPE_BYTES, EventStreamParser.HEADER_TYPE_STRING):
                if offset + 2 > len(headers_data):
                    break
                value_length = struct.unpack('>H', headers_data[offset:offset + 2])[0]
                offset += 2
                if offset + value_length > len(headers_data):
                    break
                raw = headers_data[offset:offset + value_length]
                value = raw.decode('utf-8') if value_type == EventStreamParser.HEADER_TYPE_STRING else raw
                offset += value_length
            elif value_type == EventStreamParser.HEADER_TYPE_UUID:
                if offset + 16 > len(headers_data):
                    break
                value = headers_data[offset:offset + 16].hex()
                offset += 16
            else:
                logger.warning(f"Unknown header type: {value_type}")
                break

            headers[name] = value

        return headers

    @staticmethod
    def parse_message(data: bytes, validate_crc: bool = True) -> Optional[Dict[str, Any]]:
        """Parse single Event Stream message with optional CRC validation."""
        try:
            if len(data) < 16:
                return None

            total_length = struct.unpack('>I', data[0:4])[0]
            headers_length = struct.unpack('>I', data[4:8])[0]

            if len(data) < total_length:
                logger.warning(f"Incomplete message: expected {total_length} bytes, got {len(data)}")
                return None

            if validate_crc:
                prelude_crc_expected = struct.unpack('>I', data[8:12])[0]
                prelude_crc_actual = crc32c(data[0:8])
                if prelude_crc_expected != prelude_crc_actual:
                    logger.warning(f"Prelude CRC mismatch: {prelude_crc_expected:#x} != {prelude_crc_actual:#x}")
                    return None

                message_crc_expected = struct.unpack('>I', data[-4:])[0]
                message_crc_actual = crc32c(data[:-4])
                if message_crc_expected != message_crc_actual:
                    logger.warning(f"Message CRC mismatch: {message_crc_expected:#x} != {message_crc_actual:#x}")
                    return None

            headers_data = data[12:12 + headers_length]
            headers = EventStreamParser.parse_headers(headers_data)

            payload_start = 12 + headers_length
            payload_end = total_length - 4
            payload_data = data[payload_start:payload_end]

            payload = None
            if payload_data:
                try:
                    payload = json.loads(payload_data.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = payload_data

            return {
                'headers': headers,
                'payload': payload,
                'total_length': total_length
            }

        except Exception as e:
            logger.error(f"Failed to parse message: {e}", exc_info=True)
            return None

    @staticmethod
    async def parse_stream(byte_stream: AsyncIterator[bytes], validate_crc: bool = True) -> AsyncIterator[Dict[str, Any]]:
        """Parse byte stream using state machine decoder with CRC validation."""
        decoder = EventStreamDecoder(max_errors=5, validate_crc=validate_crc)

        async for chunk in byte_stream:
            messages = decoder.feed(chunk)
            for message in messages:
                yield message

            if decoder.state == DecoderState.STOPPED:
                logger.error("Decoder stopped due to errors")
                break

def extract_event_info(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract event information from parsed message."""
    headers = message.get('headers', {})
    payload = message.get('payload')
    
    event_type = headers.get(':event-type') or headers.get('event-type')
    content_type = headers.get(':content-type') or headers.get('content-type')
    message_type = headers.get(':message-type') or headers.get('message-type')
    
    return {
        'event_type': event_type,
        'content_type': content_type,
        'message_type': message_type,
        'payload': payload
    }

def _sse_format(event_type: str, data: Dict[str, Any]) -> str:
    """Format SSE event."""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"

def build_message_start(conversation_id: str, model: str = "claude-sonnet-4.5", input_tokens: int = 0) -> str:
    """Build message_start SSE event."""
    data = {
        "type": "message_start",
        "message": {
            "id": conversation_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0}
        }
    }
    return _sse_format("message_start", data)

def build_content_block_start(index: int, block_type: str = "text") -> str:
    """Build content_block_start SSE event."""
    if block_type == "text":
        block_payload = {"type": "text", "text": ""}
    elif block_type == "thinking":
        block_payload = {"type": "thinking", "thinking": ""}
    else:
        block_payload = {"type": block_type}
    data = {
        "type": "content_block_start",
        "index": index,
        "content_block": block_payload
    }
    return _sse_format("content_block_start", data)

def build_content_block_delta(index: int, text: str, delta_type: str = "text_delta", field_name: str = "text") -> str:
    """Build content_block_delta SSE event."""
    delta = {"type": delta_type}
    if field_name:
        delta[field_name] = text
    data = {
        "type": "content_block_delta",
        "index": index,
        "delta": delta
    }
    return _sse_format("content_block_delta", data)

def build_content_block_stop(index: int) -> str:
    """Build content_block_stop SSE event."""
    data = {
        "type": "content_block_stop",
        "index": index
    }
    return _sse_format("content_block_stop", data)

def build_ping() -> str:
    """Build ping SSE event."""
    data = {"type": "ping"}
    return _sse_format("ping", data)

def build_message_stop(input_tokens: int, output_tokens: int, stop_reason: Optional[str] = None) -> str:
    """Build message_delta and message_stop SSE events."""
    delta_data = {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason or "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens}
    }
    delta_event = _sse_format("message_delta", delta_data)
    
    stop_data = {
        "type": "message_stop"
    }
    stop_event = _sse_format("message_stop", stop_data)
    
    return delta_event + stop_event

def build_tool_use_start(index: int, tool_use_id: str, tool_name: str) -> str:
    """Build tool_use content_block_start SSE event."""
    data = {
        "type": "content_block_start",
        "index": index,
        "content_block": {
            "type": "tool_use",
            "id": tool_use_id,
            "name": tool_name,
            "input": {}
        }
    }
    return _sse_format("content_block_start", data)

def build_tool_use_input_delta(index: int, input_json_delta: str) -> str:
    """Build tool_use input_json_delta SSE event."""
    data = {
        "type": "content_block_delta",
        "index": index,
        "delta": {
            "type": "input_json_delta",
            "partial_json": input_json_delta
        }
    }
    return _sse_format("content_block_delta", data)
