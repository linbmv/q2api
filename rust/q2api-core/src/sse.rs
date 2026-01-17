use serde_json::{json, Value};

#[derive(Debug, Clone)]
pub struct SseEvent {
    pub event_type: String,
    pub data: Value,
}

impl SseEvent {
    pub fn format(&self) -> String {
        let json_data = serde_json::to_string(&self.data).unwrap_or_default();
        format!("event: {}\ndata: {}\n\n", self.event_type, json_data)
    }
}

pub struct SseBuilder;

impl SseBuilder {
    pub fn message_start(conversation_id: &str, model: &str, input_tokens: u32) -> SseEvent {
        SseEvent {
            event_type: "message_start".to_string(),
            data: json!({
                "type": "message_start",
                "message": {
                    "id": conversation_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": null,
                    "stop_sequence": null,
                    "usage": {"input_tokens": input_tokens, "output_tokens": 0}
                }
            }),
        }
    }

    pub fn content_block_start(index: u32, block_type: &str) -> SseEvent {
        let content_block = match block_type {
            "text" => json!({"type": "text", "text": ""}),
            "thinking" => json!({"type": "thinking", "thinking": ""}),
            _ => json!({"type": block_type}),
        };

        SseEvent {
            event_type: "content_block_start".to_string(),
            data: json!({
                "type": "content_block_start",
                "index": index,
                "content_block": content_block
            }),
        }
    }

    pub fn content_block_delta(index: u32, text: &str, delta_type: &str, field_name: &str) -> SseEvent {
        let mut delta = json!({"type": delta_type});
        if !field_name.is_empty() {
            delta[field_name] = json!(text);
        }

        SseEvent {
            event_type: "content_block_delta".to_string(),
            data: json!({
                "type": "content_block_delta",
                "index": index,
                "delta": delta
            }),
        }
    }

    pub fn content_block_stop(index: u32) -> SseEvent {
        SseEvent {
            event_type: "content_block_stop".to_string(),
            data: json!({
                "type": "content_block_stop",
                "index": index
            }),
        }
    }

    pub fn ping() -> SseEvent {
        SseEvent {
            event_type: "ping".to_string(),
            data: json!({"type": "ping"}),
        }
    }

    pub fn message_stop(_input_tokens: u32, output_tokens: u32, stop_reason: Option<&str>) -> String {
        let delta_event = SseEvent {
            event_type: "message_delta".to_string(),
            data: json!({
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason.unwrap_or("end_turn"), "stop_sequence": null},
                "usage": {"output_tokens": output_tokens}
            }),
        };

        let stop_event = SseEvent {
            event_type: "message_stop".to_string(),
            data: json!({"type": "message_stop"}),
        };

        format!("{}{}", delta_event.format(), stop_event.format())
    }

    pub fn tool_use_start(index: u32, tool_use_id: &str, tool_name: &str) -> SseEvent {
        SseEvent {
            event_type: "content_block_start".to_string(),
            data: json!({
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": tool_name,
                    "input": {}
                }
            }),
        }
    }

    pub fn tool_use_input_delta(index: u32, input_json_delta: &str) -> SseEvent {
        SseEvent {
            event_type: "content_block_delta".to_string(),
            data: json!({
                "type": "content_block_delta",
                "index": index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": input_json_delta
                }
            }),
        }
    }
}
