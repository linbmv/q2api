use crate::error::ParseError;
use bytes::{Buf, BytesMut};
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecoderState {
    Ready,
    Parsing,
    Recovering,
    Stopped,
}

#[derive(Debug, Clone)]
pub struct ParsedMessage {
    pub headers: HashMap<String, Value>,
    pub payload: Option<Value>,
    pub total_length: u32,
}

pub struct EventStreamDecoder {
    state: DecoderState,
    buffer: BytesMut,
    error_count: u32,
    max_errors: u32,
    validate_crc: bool,
    pub messages_parsed: u64,
    pub crc_errors: u64,
}

impl EventStreamDecoder {
    pub fn new(max_errors: u32, validate_crc: bool) -> Self {
        Self {
            state: DecoderState::Ready,
            buffer: BytesMut::with_capacity(64 * 1024),
            error_count: 0,
            max_errors,
            validate_crc,
            messages_parsed: 0,
            crc_errors: 0,
        }
    }

    pub fn state(&self) -> DecoderState {
        self.state
    }

    pub fn feed(&mut self, data: &[u8]) -> Vec<ParsedMessage> {
        if self.state == DecoderState::Stopped {
            return Vec::new();
        }

        self.buffer.extend_from_slice(data);
        let mut messages = Vec::new();

        loop {
            if self.state == DecoderState::Recovering {
                if !self.try_recover() {
                    break;
                }
                self.state = DecoderState::Ready;
            }

            if self.buffer.len() < 12 {
                break;
            }

            self.state = DecoderState::Parsing;
            match self.try_parse_message() {
                Ok(Some(msg)) => {
                    self.state = DecoderState::Ready;
                    self.error_count = 0;
                    self.messages_parsed += 1;
                    messages.push(msg);
                }
                Ok(None) => break,
                Err(_) => {
                    self.error_count += 1;
                    if self.error_count >= self.max_errors {
                        self.state = DecoderState::Stopped;
                        break;
                    }
                    self.state = DecoderState::Recovering;
                }
            }
        }

        messages
    }

    fn try_parse_message(&mut self) -> Result<Option<ParsedMessage>, ParseError> {
        let total_length = u32::from_be_bytes([
            self.buffer[0],
            self.buffer[1],
            self.buffer[2],
            self.buffer[3],
        ]);

        if total_length < 16 || total_length > 16 * 1024 * 1024 {
            return Err(ParseError::InvalidLength(total_length));
        }

        if self.buffer.len() < total_length as usize {
            return Ok(None);
        }

        let message_data = self.buffer.split_to(total_length as usize);

        if self.validate_crc {
            let prelude_crc_expected = u32::from_be_bytes([
                message_data[8],
                message_data[9],
                message_data[10],
                message_data[11],
            ]);
            let prelude_crc_actual = crc32c::crc32c(&message_data[0..8]);
            if prelude_crc_expected != prelude_crc_actual {
                self.crc_errors += 1;
                return Err(ParseError::PreludeCrcMismatch {
                    expected: prelude_crc_expected,
                    actual: prelude_crc_actual,
                });
            }

            let msg_len = message_data.len();
            let message_crc_expected = u32::from_be_bytes([
                message_data[msg_len - 4],
                message_data[msg_len - 3],
                message_data[msg_len - 2],
                message_data[msg_len - 1],
            ]);
            let message_crc_actual = crc32c::crc32c(&message_data[..msg_len - 4]);
            if message_crc_expected != message_crc_actual {
                self.crc_errors += 1;
                return Err(ParseError::MessageCrcMismatch {
                    expected: message_crc_expected,
                    actual: message_crc_actual,
                });
            }
        }

        let headers_length = u32::from_be_bytes([
            message_data[4],
            message_data[5],
            message_data[6],
            message_data[7],
        ]) as usize;

        let headers = parse_headers(&message_data[12..12 + headers_length])?;

        let payload_start = 12 + headers_length;
        let payload_end = total_length as usize - 4;
        let payload_data = &message_data[payload_start..payload_end];

        let payload = if payload_data.is_empty() {
            None
        } else {
            match serde_json::from_slice(payload_data) {
                Ok(v) => Some(v),
                Err(_) => Some(Value::String(
                    String::from_utf8_lossy(payload_data).into_owned(),
                )),
            }
        };

        Ok(Some(ParsedMessage {
            headers,
            payload,
            total_length,
        }))
    }

    fn try_recover(&mut self) -> bool {
        if self.buffer.len() < 12 {
            return false;
        }

        self.buffer.advance(1);

        for i in 0..self.buffer.len().saturating_sub(11) {
            let total_length = u32::from_be_bytes([
                self.buffer[i],
                self.buffer[i + 1],
                self.buffer[i + 2],
                self.buffer[i + 3],
            ]);

            if (16..=16 * 1024 * 1024).contains(&total_length) && self.buffer.len() >= i + 12 {
                let prelude = &self.buffer[i..i + 8];
                let prelude_crc = u32::from_be_bytes([
                    self.buffer[i + 8],
                    self.buffer[i + 9],
                    self.buffer[i + 10],
                    self.buffer[i + 11],
                ]);

                if crc32c::crc32c(prelude) == prelude_crc {
                    self.buffer.advance(i);
                    return true;
                }
            }
        }

        if self.buffer.len() > 16 * 1024 {
            let trim = self.buffer.len() - 1024;
            self.buffer.advance(trim);
        }

        false
    }

    pub fn reset(&mut self) {
        self.state = DecoderState::Ready;
        self.buffer.clear();
        self.error_count = 0;
    }
}

fn parse_headers(data: &[u8]) -> Result<HashMap<String, Value>, ParseError> {
    let mut headers = HashMap::new();
    let mut offset = 0;

    while offset < data.len() {
        if offset >= data.len() {
            break;
        }
        let name_length = data[offset] as usize;
        offset += 1;

        if offset + name_length > data.len() {
            break;
        }
        let name = String::from_utf8(data[offset..offset + name_length].to_vec())
            .map_err(|e| ParseError::Utf8Error(e))?;
        offset += name_length;

        if offset >= data.len() {
            break;
        }
        let value_type = data[offset];
        offset += 1;

        let value = match value_type {
            0 => Value::Bool(true),
            1 => Value::Bool(false),
            2 => {
                if offset >= data.len() {
                    break;
                }
                let v = data[offset] as i64;
                offset += 1;
                Value::Number(v.into())
            }
            3 => {
                if offset + 2 > data.len() {
                    break;
                }
                let v = i16::from_be_bytes([data[offset], data[offset + 1]]) as i64;
                offset += 2;
                Value::Number(v.into())
            }
            4 => {
                if offset + 4 > data.len() {
                    break;
                }
                let v = i32::from_be_bytes([
                    data[offset],
                    data[offset + 1],
                    data[offset + 2],
                    data[offset + 3],
                ]) as i64;
                offset += 4;
                Value::Number(v.into())
            }
            5 | 8 => {
                if offset + 8 > data.len() {
                    break;
                }
                let v = i64::from_be_bytes([
                    data[offset],
                    data[offset + 1],
                    data[offset + 2],
                    data[offset + 3],
                    data[offset + 4],
                    data[offset + 5],
                    data[offset + 6],
                    data[offset + 7],
                ]);
                offset += 8;
                Value::Number(v.into())
            }
            6 | 7 => {
                if offset + 2 > data.len() {
                    break;
                }
                let value_length =
                    u16::from_be_bytes([data[offset], data[offset + 1]]) as usize;
                offset += 2;
                if offset + value_length > data.len() {
                    break;
                }
                let raw = &data[offset..offset + value_length];
                offset += value_length;
                if value_type == 7 {
                    Value::String(String::from_utf8_lossy(raw).into_owned())
                } else {
                    Value::String(hex::encode(raw))
                }
            }
            9 => {
                if offset + 16 > data.len() {
                    break;
                }
                let uuid_bytes = &data[offset..offset + 16];
                offset += 16;
                Value::String(hex::encode(uuid_bytes))
            }
            _ => return Err(ParseError::InvalidHeaderType(value_type)),
        };

        headers.insert(name, value);
    }

    Ok(headers)
}
