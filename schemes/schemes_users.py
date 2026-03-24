from pydantic import BaseModel, ConfigDict, EmailStr, Field, RootModel
from typing import Optional, List
from datetime import datetime, date, time
from decimal import Decimal
from models.user_models import UserRole

# --- USER SCHEMAS ---
class UserCreateRequest(BaseModel):
    email: EmailStr
    name: str
    surname: str
    password: str
    company_code: str = "oddiy"
    telegram_id: Optional[str] = None
    default_salary: Optional[Decimal] = Decimal('0.00')
    role: UserRole = UserRole.customer
    job_title: Optional[str] = None
    profile_image: Optional[str] = None
    is_active: bool = True

class UserUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    surname: Optional[str] = None
    password: Optional[str] = None
    company_code: Optional[str] = None
    telegram_id: Optional[str] = None
    default_salary: Optional[Decimal] = None
    role: Optional[UserRole] = None
    job_title: Optional[str] = None
    profile_image: Optional[str] = None
    is_active: Optional[bool] = None

class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    surname: str
    company_code: str
    telegram_id: Optional[str]
    default_salary: Decimal
    role: str
    job_title: Optional[str]
    profile_image: Optional[str] = None
    is_active: bool
    permissions: List[str] = []

class UserListResponse(BaseModel):
    users: List[UserResponse]
    statistics: dict

class UserToggleResponse(BaseModel):
    is_active: bool
    active_user_count: int
    inactive_user_count: int

# --- MESSAGE SCHEMAS ---
class MessageToAllRequest(BaseModel):
    subject: str
    body: str

class MessageToUserRequest(BaseModel):
    receiver_id: int
    subject: str
    body: str

class MessageResponse(BaseModel):
    id: int
    receiver_name: str
    receiver_email: str
    subject: str
    body: str
    sent_at: str

class MessageListResponse(BaseModel):
    messages: List[MessageResponse]


class MyMessageResponse(BaseModel):
    id: int
    sender_id: int
    sender_name: str
    sender_email: str
    subject: str
    body: str
    sent_at: str


class MyMessageListResponse(BaseModel):
    messages: List[MyMessageResponse]

# --- PAYMENT SCHEMAS ---
class PaymentCreateRequest(BaseModel):
    project: str
    date: date
    summ: Decimal
    payment: bool = False

class PaymentUpdateRequest(BaseModel):
    project: Optional[str] = None
    date: Optional[date] = None
    summ: Optional[Decimal] = None
    payment: Optional[bool] = None

class PaymentResponse(BaseModel):
    id: int
    project: str
    date: str
    summ: float
    payment: bool

class PaymentListResponse(BaseModel):
    payments: List[PaymentResponse]

class PaymentToggleResponse(BaseModel):
    message: str
    payment_id: int
    payment_status: bool


class CompanyRecurringPaymentCreateRequest(BaseModel):
    title: str
    amount: Decimal
    payment_day: int
    payment_time: time
    note: Optional[str] = None
    is_active: bool = True


class CompanyRecurringPaymentUpdateRequest(BaseModel):
    title: Optional[str] = None
    amount: Optional[Decimal] = None
    payment_day: Optional[int] = None
    payment_time: Optional[time] = None
    note: Optional[str] = None
    is_active: Optional[bool] = None

# --- GENERAL RESPONSE SCHEMAS ---
class SuccessResponse(BaseModel):
    message: str

class ErrorResponse(BaseModel):
    detail: str

class CreateResponse(BaseModel):
    message: str
    id: int

# --- DASHBOARD STATISTICS SCHEMA ---
class DashboardStatistics(BaseModel):
    user_count: int
    messages_count: int
    active_user_count: int
    inactive_user_count: int

class DashboardResponse(BaseModel):
    users: List[dict]
    statistics: DashboardStatistics


# schemes_users.py

from typing import Dict


class UserPermissionUpdateRequest(RootModel[Dict[str, bool]]):
    """
    User permissions yangilash uchun schema
    Body to'g'ridan-to'g'ri {"crm": true, "projects": false} formatida yuboriladi
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ceo": True,
                "payment_list": False,
                "project_toggle": True,
                "projects": True,
                "crm": False,
                "finance_list": True,
                "update_list": True,
                "company_payments": False,
            }
        }
    )

    def to_dict(self) -> Dict[str, bool]:
        return {
            str(key).strip(): bool(value)
            for key, value in self.root.items()
            if str(key).strip()
        }


class UserPermissionAddRequest(RootModel[Dict[str, bool]]):
    """
    Foydalanuvchiga ruxsat qo'shish uchun schema
    Body to'g'ridan-to'g'ri {"crm": true} formatida yuboriladi
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "crm": True,
                "projects": True
            }
        }
    )

    def to_dict(self) -> Dict[str, bool]:
        return {
            str(key).strip(): bool(value)
            for key, value in self.root.items()
            if str(key).strip()
        }

class UserPermissionResponse(BaseModel):
    """
    User permissions response schema
    """
    user_id: int
    user_email: str
    user_name: str
    permissions: Dict[str, bool] = Field(..., description="Sahifa ruxsatlari (true/false)")
    # permissions_display: Dict[str, bool] = Field(..., description="Ko'rsatish nomlari")
    active_permissions_count: int
    total_available_pages: int

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": 1,
                "user_email": "user@example.com",
                "user_name": "John Doe",
                "permissions": {
                    "ceo": True,
                    "payment_list": False,
                    "project_toggle": True,
                    "projects": True,
                    "crm": False,
                    "finance_list": True,
                    "update_list": True,
                    "company_payments": False
                },
                "permissions_display": {
                    "Dashboard": True,
                    "Payment": False,
                    "Wordpress": True,
                    "Projects": True,
                    "Sales CRM": False,
                    "Finance": True,
                    "Update": True
                },
                "active_permissions_count": 5,
                "total_available_pages": 8
            }
        }
    )


class UserPermissionsOverviewResponse(BaseModel):
    """
    Barcha userlar permissions overview uchun schema
    """
    user_id: int
    email: str
    name: str
    role: str
    job_title: Optional[str]
    is_active: bool
    permissions: List[str]
    permissions_display: List[str]
    permissions_count: int


class AllUsersPermissionsResponse(BaseModel):
    """
    Barcha userlar permissions response schema
    """
    users: List[UserPermissionsOverviewResponse]
    total_users: int
    available_pages: List[str]
    summary: Dict[str, int]


class SuccessResponse(BaseModel):
    """
    Muvaffaqiyatli operatsiya uchun response
    """
    message: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Operatsiya muvaffaqiyatli bajarildi"
            }
        }
    )



# --- DAILY METRICS SCHEMES ---
class TodayCustomerInfo(BaseModel):
    id: int
    full_name: str
    platform: str
    username: Optional[str] = None
    phone_number: str
    status: str
    assistant_name: Optional[str] = None
    created_at: str  # ISO

    class Config:
        from_attributes = True


class DailyMetricsResponse(BaseModel):
    today_customers: List[TodayCustomerInfo]
    need_to_call_count: int
    total_balance_uzs: float
    total_balance_formatted: str
    due_payments_today: int

    class Config:
        from_attributes = True
