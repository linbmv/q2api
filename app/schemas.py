from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel

class ClaudeMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ClaudeTool(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    type: Optional[str] = None
    max_uses: Optional[int] = None

    def is_web_search(self) -> bool:
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

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
