use thiserror::Error;

#[derive(Error, Debug)]
pub enum ParseError {
    #[error("Invalid message length: {0}")]
    InvalidLength(u32),

    #[error("Prelude CRC mismatch: expected {expected:#x}, got {actual:#x}")]
    PreludeCrcMismatch { expected: u32, actual: u32 },

    #[error("Message CRC mismatch: expected {expected:#x}, got {actual:#x}")]
    MessageCrcMismatch { expected: u32, actual: u32 },

    #[error("Incomplete message: expected {expected} bytes, got {actual}")]
    IncompleteMessage { expected: usize, actual: usize },

    #[error("Invalid header type: {0}")]
    InvalidHeaderType(u8),

    #[error("Header parse error at offset {0}")]
    HeaderParseError(usize),

    #[error("Max errors reached: {0}")]
    MaxErrorsReached(u32),

    #[error("UTF-8 decode error: {0}")]
    Utf8Error(#[from] std::string::FromUtf8Error),

    #[error("JSON parse error: {0}")]
    JsonError(#[from] serde_json::Error),
}
