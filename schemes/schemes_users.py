from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, date
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




class UserPermissionRequest(BaseModel):
    page_names: List[str]  # ["ceo", "payment_list", "project_toggle", "crm", "finance_list"]

class UserPermissionResponse(BaseModel):
    user_id: int
    user_email: str
    permissions: List[str]
    message: str