from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from models.projects_models import CardPriority


class UserSummaryResponse(BaseModel):
    id: int
    name: str
    surname: str
    email: str


class BoardCardFileResponse(BaseModel):
    id: int
    card_id: int
    created_at: datetime
    url_path: str


class CardResponse(BaseModel):
    id: int
    column_id: int
    title: str
    description: Optional[str]
    order: int
    priority: CardPriority
    assignee_id: Optional[int]
    due_date: Optional[date]
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime
    assignee: Optional[UserSummaryResponse] = None
    created_by_user: Optional[UserSummaryResponse] = None
    files: List[BoardCardFileResponse] = Field(default_factory=list)


class CardDetailResponse(CardResponse):
    board_id: int
    project_id: int


class BoardColumnResponse(BaseModel):
    id: int
    board_id: int
    name: str
    order: int
    color: Optional[str]
    created_at: datetime
    cards: List[CardResponse] = Field(default_factory=list)


class BoardListItemResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: Optional[str]
    created_by: Optional[int]
    created_at: datetime
    is_archived: bool
    created_by_user: Optional[UserSummaryResponse] = None


class BoardListResponse(BaseModel):
    boards: List[BoardListItemResponse]
    total_count: int


class BoardDetailResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: Optional[str]
    created_by: Optional[int]
    created_at: datetime
    is_archived: bool
    created_by_user: Optional[UserSummaryResponse] = None
    columns: List[BoardColumnResponse] = Field(default_factory=list)


class ProjectSummaryResponse(BaseModel):
    id: int
    project_name: str
    project_description: Optional[str]
    project_url: Optional[str]
    project_image: Optional[str]
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    board_count: int = 0
    created_by_user: Optional[UserSummaryResponse] = None


class ProjectDetailResponse(BaseModel):
    id: int
    project_name: str
    project_description: Optional[str]
    project_url: Optional[str]
    project_image: Optional[str]
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime
    created_by_user: Optional[UserSummaryResponse] = None
    members: List[UserSummaryResponse] = Field(default_factory=list)
    boards: List[BoardListItemResponse] = Field(default_factory=list)


class ProjectListResponse(BaseModel):
    projects: List[ProjectSummaryResponse]
    total_count: int


class ProjectCreateRequest(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=255)
    project_description: Optional[str] = None
    project_url: Optional[str] = Field(None, max_length=500)
    project_image: Optional[str] = Field(None, max_length=500)
    member_ids: List[int] = Field(default_factory=list)

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, value: str) -> str:
        return value.strip()


class ProjectUpdateRequest(BaseModel):
    project_name: Optional[str] = Field(None, min_length=1, max_length=255)
    project_description: Optional[str] = None
    project_url: Optional[str] = Field(None, max_length=500)
    project_image: Optional[str] = Field(None, max_length=500)
    member_ids: Optional[List[int]] = None

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip()


class BoardCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return value.strip()


class BoardUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip()


class ColumnCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    color: Optional[str] = Field(None, min_length=7, max_length=7)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return value.strip()


class ColumnUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    color: Optional[str] = Field(None, min_length=7, max_length=7)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip()


class ColumnMoveRequest(BaseModel):
    order: int = Field(..., ge=0)


class CardCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    order: Optional[int] = Field(None, ge=0)
    priority: CardPriority = CardPriority.medium
    assignee_id: Optional[int] = None
    due_date: Optional[date] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return value.strip()


class CardUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    priority: Optional[CardPriority] = None
    assignee_id: Optional[int] = None
    due_date: Optional[date] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip()


class CardMoveRequest(BaseModel):
    column_id: int
    order: int = Field(..., ge=0)
