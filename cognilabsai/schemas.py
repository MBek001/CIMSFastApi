from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class IntegrationConfigPayload(BaseModel):
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    openai_base_url: Optional[str] = None
    system_prompt: Optional[str] = None
    instagram_access_token: Optional[str] = None
    instagram_business_id: Optional[str] = None
    instagram_verify_token: Optional[str] = None
    telegram_api_id: Optional[str] = None
    telegram_api_hash: Optional[str] = None
    telegram_session: Optional[str] = None
    websocket_api_key: Optional[str] = None


class IntegrationConfigResponse(IntegrationConfigPayload):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConversationItem(BaseModel):
    id: int
    channel: str
    chat_mode: str
    supports_ai: bool
    client_external_id: str
    client_username: Optional[str] = None
    client_full_name: Optional[str] = None
    client_avatar_url: Optional[str] = None
    instagram_business_id: Optional[str] = None
    ai_enabled: bool
    pause_reason: Optional[str] = None
    paused_until: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    last_message_preview: Optional[str] = None
    last_operator_user_id: Optional[int] = None
    last_operator_name: Optional[str] = None
    is_imported: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MessageItem(BaseModel):
    id: int
    conversation_id: int
    channel: str
    sender_type: str
    operator_user_id: Optional[int] = None
    operator_name_snapshot: Optional[str] = None
    client_external_id: Optional[str] = None
    instagram_message_id: Optional[str] = None
    telegram_message_id: Optional[str] = None
    text: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    conversation_id: int
    text: str = Field(min_length=1)


class PauseConversationRequest(BaseModel):
    conversation_id: int


class PauseUntilRequest(BaseModel):
    conversation_id: int
    paused_until: datetime


class TelegramStartConversationRequest(BaseModel):
    peer: str = Field(min_length=1)
    text: str = Field(min_length=1)
    client_full_name: Optional[str] = None


class TelegramSearchResult(BaseModel):
    peer: str
    external_id: str
    username: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    existing_conversation_id: Optional[int] = None


class TelegramSearchMatch(BaseModel):
    peer: str
    external_id: str
    username: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    existing_conversation_id: Optional[int] = None


class TelegramSearchListResponse(BaseModel):
    query: str
    items: list[TelegramSearchMatch]


class ImportConversationsRequest(BaseModel):
    folder_path: str = "/home/akhmad/PyCharmMiscProject/project/conversations"


class ImportConversationsResponse(BaseModel):
    imported_files: int
    skipped_files: int
    created_conversations: int
    created_messages: int
    source_type: Optional[str] = None


class GenericMessageResponse(BaseModel):
    message: str
