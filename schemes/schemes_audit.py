from typing import Any, Optional

from pydantic import BaseModel, Field


class AuditLogResponse(BaseModel):
    id: int
    created_at: str
    actor_user_id: Optional[int]
    actor_email: Optional[str]
    actor_name: Optional[str]
    module: str
    table_name: str
    entity_type: str
    entity_id: Optional[str]
    action: str
    summary: Optional[str]
    before_data: Optional[dict[str, Any]]
    after_data: Optional[dict[str, Any]]
    changed_fields: list[str] = Field(default_factory=list)
    request_id: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    is_system_action: bool


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
