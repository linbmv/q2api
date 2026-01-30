"""Token 计数工具模块 - 使用 tiktoken 进行精确计数"""
import logging

logger = logging.getLogger(__name__)

try:
    import tiktoken
    ENCODING = tiktoken.get_encoding("cl100k_base")
    logger.info("tiktoken initialized successfully with cl100k_base encoding")
except Exception as e:
    ENCODING = None
    logger.warning(f"Failed to initialize tiktoken: {e}. Token counting will return 0.")

def count_tokens(text: str) -> int:
    """使用 tiktoken 精确计数 tokens

    Args:
        text: 要计数的文本

    Returns:
        token 数量，如果 tiktoken 不可用则使用 fallback 估算
    """
    if not text:
        return 0
    if not ENCODING:
        # Fallback: rough estimate (1 token ≈ 4 chars)
        return len(text) // 4
    return len(ENCODING.encode(text))
