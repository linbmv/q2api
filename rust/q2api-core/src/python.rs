use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::decoder::{DecoderState, EventStreamDecoder};
use crate::sse::SseBuilder;

#[pyclass(name = "DecoderState")]
#[derive(Clone)]
pub struct PyDecoderState(DecoderState);

#[pymethods]
impl PyDecoderState {
    #[getter]
    fn name(&self) -> &str {
        match self.0 {
            DecoderState::Ready => "READY",
            DecoderState::Parsing => "PARSING",
            DecoderState::Recovering => "RECOVERING",
            DecoderState::Stopped => "STOPPED",
        }
    }

    fn __repr__(&self) -> String {
        format!("DecoderState.{}", self.name())
    }
}

#[pyclass(name = "EventStreamDecoder")]
pub struct PyEventStreamDecoder {
    inner: EventStreamDecoder,
}

#[pymethods]
impl PyEventStreamDecoder {
    #[new]
    #[pyo3(signature = (max_errors=3, validate_crc=true))]
    fn new(max_errors: u32, validate_crc: bool) -> Self {
        Self {
            inner: EventStreamDecoder::new(max_errors, validate_crc),
        }
    }

    #[getter]
    fn state(&self) -> PyDecoderState {
        PyDecoderState(self.inner.state())
    }

    #[getter]
    fn messages_parsed(&self) -> u64 {
        self.inner.messages_parsed
    }

    #[getter]
    fn crc_errors(&self) -> u64 {
        self.inner.crc_errors
    }

    fn feed(&mut self, py: Python<'_>, data: &[u8]) -> PyResult<Py<PyList>> {
        let messages = self.inner.feed(data);
        let list = PyList::empty(py);

        for msg in messages {
            let dict = PyDict::new(py);

            let headers_dict = PyDict::new(py);
            for (k, v) in msg.headers {
                headers_dict.set_item(k, json_to_py(py, &v)?)?;
            }
            dict.set_item("headers", headers_dict)?;

            if let Some(payload) = msg.payload {
                dict.set_item("payload", json_to_py(py, &payload)?)?;
            } else {
                dict.set_item("payload", py.None())?;
            }

            dict.set_item("total_length", msg.total_length)?;
            list.append(dict)?;
        }

        Ok(list.into())
    }

    fn reset(&mut self) {
        self.inner.reset();
    }
}

#[pyfunction]
fn compute_crc32c(data: &[u8]) -> u32 {
    ::crc32c::crc32c(data)
}

#[pyfunction]
fn build_message_start(conversation_id: &str, model: &str, input_tokens: u32) -> String {
    SseBuilder::message_start(conversation_id, model, input_tokens).format()
}

#[pyfunction]
fn build_content_block_start(index: u32, block_type: &str) -> String {
    SseBuilder::content_block_start(index, block_type).format()
}

#[pyfunction]
#[pyo3(signature = (index, text, delta_type="text_delta", field_name="text"))]
fn build_content_block_delta(index: u32, text: &str, delta_type: &str, field_name: &str) -> String {
    SseBuilder::content_block_delta(index, text, delta_type, field_name).format()
}

#[pyfunction]
fn build_content_block_stop(index: u32) -> String {
    SseBuilder::content_block_stop(index).format()
}

#[pyfunction]
fn build_ping() -> String {
    SseBuilder::ping().format()
}

#[pyfunction]
#[pyo3(signature = (input_tokens, output_tokens, stop_reason=None))]
fn build_message_stop(input_tokens: u32, output_tokens: u32, stop_reason: Option<&str>) -> String {
    SseBuilder::message_stop(input_tokens, output_tokens, stop_reason)
}

#[pyfunction]
fn build_tool_use_start(index: u32, tool_use_id: &str, tool_name: &str) -> String {
    SseBuilder::tool_use_start(index, tool_use_id, tool_name).format()
}

#[pyfunction]
fn build_tool_use_input_delta(index: u32, input_json_delta: &str) -> String {
    SseBuilder::tool_use_input_delta(index, input_json_delta).format()
}

#[pymodule]
fn q2api_core(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_class::<PyDecoderState>()?;
    m.add_class::<PyEventStreamDecoder>()?;
    m.add_function(wrap_pyfunction!(compute_crc32c, m)?)?;
    m.add_function(wrap_pyfunction!(build_message_start, m)?)?;
    m.add_function(wrap_pyfunction!(build_content_block_start, m)?)?;
    m.add_function(wrap_pyfunction!(build_content_block_delta, m)?)?;
    m.add_function(wrap_pyfunction!(build_content_block_stop, m)?)?;
    m.add_function(wrap_pyfunction!(build_ping, m)?)?;
    m.add_function(wrap_pyfunction!(build_message_stop, m)?)?;
    m.add_function(wrap_pyfunction!(build_tool_use_start, m)?)?;
    m.add_function(wrap_pyfunction!(build_tool_use_input_delta, m)?)?;
    Ok(())
}

fn json_to_py(py: Python<'_>, value: &serde_json::Value) -> PyResult<PyObject> {
    use pyo3::ToPyObject;

    Ok(match value {
        serde_json::Value::Null => py.None(),
        serde_json::Value::Bool(b) => b.to_object(py),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.to_object(py)
            } else if let Some(f) = n.as_f64() {
                f.to_object(py)
            } else {
                py.None()
            }
        }
        serde_json::Value::String(s) => s.to_object(py),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(json_to_py(py, item)?)?;
            }
            list.to_object(py)
        }
        serde_json::Value::Object(obj) => {
            let dict = PyDict::new(py);
            for (k, v) in obj {
                dict.set_item(k, json_to_py(py, v)?)?;
            }
            dict.to_object(py)
        }
    })
}
