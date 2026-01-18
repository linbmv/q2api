from typing import List, Optional, Union, Dict, Any, Literal
from pydantic import BaseModel, Field

class ClaudeMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ClaudeTool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    type: Optional[str] = None  # For WebSearch: "web_search_20250305"
    max_uses: Optional[int] = None  # For WebSearch: typically 8

    def is_web_search(self) -> bool:
        """Check if this is a WebSearch tool"""
        return self.type is not None and self.type.startswith("web_search")

class ClaudeRequest(BaseModel):
    model: str
    messages: List[ClaudeMessage]
    max_tokens: int = 4096
    temperature: Optional[float] = None
    tools: Optional[List[ClaudeTool]] = None
    stream: bool = False
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    thinking: Optional[Dict[str, Any]] = None
