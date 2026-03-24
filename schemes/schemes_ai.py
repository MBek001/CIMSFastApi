from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CimsAiChatHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class CimsAiChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000, description="User question for CIMS AI")
    history: List[CimsAiChatHistoryItem] = Field(default_factory=list, description="Optional recent chat history")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "question": "Asilbekning o'tgan oydagi update foizi qancha bo'lgan?",
                "history": [],
            }
        }
    )


class CimsAiChatResponse(BaseModel):
    answer: str
    used_llm: bool
    intents: List[str]
    period: Dict[str, Any]
    employee: Optional[Dict[str, Any]] = None
    context: Dict[str, Any] = Field(default_factory=dict)

