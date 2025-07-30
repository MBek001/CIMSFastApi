from pydantic import BaseModel, EmailStr
from typing import Optional
from decimal import Decimal
from models.user_models import UserRole


class UserCreate(BaseModel):
    email: EmailStr
    name: str
    surname: str
    password: str
    company_code: Optional[str] = "oddiy"
    telegram_id: Optional[str] = None
    role: Optional[UserRole] = UserRole.customer


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    surname: str
    company_code: str
    role: UserRole
    is_active: bool

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class EmailVerificationRequest(BaseModel):
    email: EmailStr


class EmailVerificationConfirm(BaseModel):
    email: EmailStr
    code: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    code: str
    new_password: str


class SuccessResponse(BaseModel):
    success: bool = True
    message: str


class RedirectResponse(BaseModel):
    redirect_url: str