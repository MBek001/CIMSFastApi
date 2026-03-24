"""
Pydantic schemas for dynamic Status and Role management
"""
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
from datetime import datetime


# ========================================
# CUSTOMER STATUS SCHEMAS
# ========================================

class CustomerStatusCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Unique status name (e.g., 'contacted')")
    display_name: str = Field(..., min_length=1, max_length=255, description="Display name (e.g., 'Contacted')")
    description: Optional[str] = Field(None, description="Status description")
    color: Optional[str] = Field(None, max_length=50, description="Color code for UI (e.g., '#FF5733')")
    order: int = Field(0, description="Display order")
    is_active: bool = Field(True, description="Is this status active?")
    is_system: bool = Field(False, description="System statuses can't be deleted")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        # Remove spaces, convert to lowercase
        return v.strip().lower().replace(' ', '_')

    @field_validator('display_name')
    @classmethod
    def validate_display_name(cls, v):
        return v.strip()


class CustomerStatusUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, max_length=50)
    order: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator('display_name')
    @classmethod
    def validate_display_name(cls, v):
        if v:
            return v.strip()
        return v


class CustomerStatusResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: Optional[str]
    color: Optional[str]
    order: int
    is_active: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


# ========================================
# USER ROLE SCHEMAS
# ========================================

class UserRoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Unique role name (e.g., 'sales_manager')")
    display_name: str = Field(..., min_length=1, max_length=255, description="Display name (e.g., 'Sales Manager')")
    description: Optional[str] = Field(None, description="Role description")
    is_active: bool = Field(True, description="Is this role active?")
    is_system: bool = Field(False, description="System roles can't be deleted")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        # Remove spaces, convert to lowercase
        return v.strip().lower().replace(' ', '_')

    @field_validator('display_name')
    @classmethod
    def validate_display_name(cls, v):
        return v.strip()


class UserRoleUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator('display_name')
    @classmethod
    def validate_display_name(cls, v):
        if v:
            return v.strip()
        return v


class UserRoleResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: Optional[str]
    is_active: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


# ========================================
# APP PAGE SCHEMAS
# ========================================

class AppPageCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Unique page key (e.g., 'crm')")
    display_name: str = Field(..., min_length=1, max_length=255, description="Display name (e.g., 'Sales CRM')")
    description: Optional[str] = Field(None, description="Page description")
    route_path: Optional[str] = Field(None, max_length=255, description="Frontend route path (e.g., '/crm')")
    order: int = Field(0, description="Display order")
    is_active: bool = Field(True, description="Is this page active?")
    is_system: bool = Field(False, description="System pages can't be deleted")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        return v.strip().lower().replace(' ', '_')

    @field_validator('display_name')
    @classmethod
    def validate_display_name(cls, v):
        return v.strip()

    @field_validator('route_path')
    @classmethod
    def validate_route_path(cls, v):
        if v is None:
            return v
        v = v.strip()
        if v and not v.startswith('/'):
            return f'/{v}'
        return v or None


class AppPageUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    route_path: Optional[str] = Field(None, max_length=255)
    order: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator('display_name')
    @classmethod
    def validate_display_name(cls, v):
        if v:
            return v.strip()
        return v

    @field_validator('route_path')
    @classmethod
    def validate_route_path(cls, v):
        if v is None:
            return v
        v = v.strip()
        if v and not v.startswith('/'):
            return f'/{v}'
        return v or None


class AppPageResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: Optional[str]
    route_path: Optional[str]
    order: int
    is_active: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


# ========================================
# SALES MANAGER ASSIGNMENT SCHEMAS
# ========================================

class SalesManagerAssignmentCreate(BaseModel):
    customer_id: int
    sales_manager_id: int


class SalesManagerAssignmentResponse(BaseModel):
    id: int
    customer_id: int
    sales_manager_id: int
    assigned_at: datetime
    assigned_by: Optional[int]
    is_active: bool


class SalesManagerInfo(BaseModel):
    id: int
    email: str
    name: str
    surname: str
    assigned_leads_count: int


# ========================================
# CONVERSION RATE SCHEMAS
# ========================================

class ConversionRateResponse(BaseModel):
    total_customers: int = Field(..., description="Total customers in last 100 leads")
    project_started_count: int = Field(..., description="Number of leads that reached 'project_started' status")
    conversion_rate: float = Field(..., description="Percentage of conversion (0-100)")
    period: str = Field(..., description="Analysis period description")


# ========================================
# IMAGE CLEANUP SCHEMAS
# ========================================

class ImageDeleteResponse(BaseModel):
    message: str
    requested_count: int
    deleted_count: int
    missing_count: int
    skipped_count: int
    deleted_paths: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    skipped_items: list[str] = Field(default_factory=list)
    cleared_references: dict[str, int] = Field(default_factory=dict)


class ImageBulkDeleteRequest(BaseModel):
    image_paths: list[str] = Field(default_factory=list, description="Delete qilinadigan image pathlar")
    category: Optional[Literal["project_images", "profil_images", "card_images"]] = Field(
        None,
        description="Bulk o'chirish uchun category"
    )
    delete_all_in_category: bool = Field(
        False,
        description="True bo'lsa category ichidagi barcha rasmlar ko'rib chiqiladi"
    )
    only_unreferenced: bool = Field(
        False,
        description="Faqat DB da reference bo'lmagan rasmlarni o'chirish"
    )
