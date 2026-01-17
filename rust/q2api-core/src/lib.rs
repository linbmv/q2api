mod decoder;
mod error;
mod sse;

pub use decoder::{DecoderState, EventStreamDecoder, ParsedMessage};
pub use error::ParseError;
pub use sse::{SseBuilder, SseEvent};

#[cfg(feature = "python")]
mod python;

#[cfg(feature = "python")]
pub use python::*;
