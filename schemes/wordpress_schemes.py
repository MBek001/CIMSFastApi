from pydantic import BaseModel
from typing import List, Optional


# --- REQUEST SCHEMAS ---
class SiteToggleRequest(BaseModel):
    action: str = "toggle"  # toggle, on, off


class WordPressProjectCreateRequest(BaseModel):
    name: str
    url: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True


class WordPressProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


# --- RESPONSE SCHEMAS ---
class SiteStatusResponse(BaseModel):
    is_site_on: bool
    message: str


class WordPressProjectResponse(BaseModel):
    id: int
    name: str
    url: Optional[str]
    description: Optional[str]
    is_active: bool


class WordPressProjectListResponse(BaseModel):
    projects: List[WordPressProjectResponse]
    total_count: int


class WordPressDashboardResponse(BaseModel):
    site_status: bool
    permissions: List[str]
    projects: List[WordPressProjectResponse]
    statistics: dict


class SuccessResponse(BaseModel):
    message: str


class CreateResponse(BaseModel):
    message: str
    id: int