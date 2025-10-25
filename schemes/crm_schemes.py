from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum
from models.admin_models import CustomerStatus


# --- ENUMS ---
class ConversationLanguageEnum(str, Enum):
    UZ = "uz"
    RU = "ru"
    EN = "en"


# --- BASE RESPONSE MODELS ---
class SuccessResponse(BaseModel):
    message: str


class CreateResponse(BaseModel):
    message: str
    id: int


# --- CUSTOMER REQUEST MODELS ---
class CustomerCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255, description="Mijozning to'liq ismi")
    platform: str = Field(..., min_length=1, max_length=255, description="Platform nomi")
    username: Optional[str] = Field(None, max_length=255, description="Foydalanuvchi nomi")
    phone_number: str = Field(..., min_length=1, max_length=20, description="Telefon raqami")
    status: CustomerStatus = Field(..., description="Mijoz holati")
    assistant_name: Optional[str] = Field(None, max_length=255, description="Yordamchi ismi")
    notes: Optional[str] = Field(None, description="Qo'shimcha eslatmalar")
    conversation_language: Optional[ConversationLanguageEnum] = Field(
        default=ConversationLanguageEnum.UZ,
        description="Suhbat tili"
    )

    @validator('phone_number')
    def validate_phone_number(cls, v):
        if not v.strip():
            raise ValueError('Telefon raqami bo\'sh bo\'lishi mumkin emas')
        return v.strip()

    @validator('full_name', 'platform')
    def validate_not_empty(cls, v):
        if not v.strip():
            raise ValueError('Bu maydon bo\'sh bo\'lishi mumkin emas')
        return v.strip()

    @validator('username', 'assistant_name')
    def validate_optional_not_empty(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Bu maydon bo\'sh bo\'lishi mumkin emas')
        return v.strip() if v else v


class CustomerUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=255, description="Mijozning to'liq ismi")
    platform: Optional[str] = Field(None, min_length=1, max_length=255, description="Platform nomi")
    username: Optional[str] = Field(None, max_length=255, description="Foydalanuvchi nomi")
    phone_number: Optional[str] = Field(None, min_length=1, max_length=20, description="Telefon raqami")
    status: Optional[CustomerStatus] = Field(None, description="Mijoz holati")
    assistant_name: Optional[str] = Field(None, max_length=255, description="Yordamchi ismi")
    notes: Optional[str] = Field(None, description="Qo'shimcha eslatmalar")
    conversation_language: Optional[ConversationLanguageEnum] = Field(None, description="Suhbat tili")

    @validator('phone_number', 'full_name', 'platform', 'username', 'assistant_name')
    def validate_not_empty(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Bu maydon bo\'sh bo\'lishi mumkin emas')
        return v.strip() if v else v


class CustomerDeleteRequest(BaseModel):
    customer_ids: List[int] = Field(..., min_items=1, description="O'chiriladigan mijozlar ID ro'yxati")


# --- CUSTOMER RESPONSE MODELS ---
class CustomerResponse(BaseModel):
    id: int
    full_name: str
    platform: str
    username: Optional[str]
    phone_number: str
    status: str
    assistant_name: Optional[str]
    notes: Optional[str]
    audio_file_id: Optional[str]
    audio_url: Optional[str] = None  # ✅ Yangi maydon qo‘shildi
    conversation_language: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


class StatusChoice(BaseModel):
    value: str
    label: str


class CustomerStatsResponse(BaseModel):
    total_customers: int
    need_to_call: int
    contacted: int
    project_started: int
    continuing: int
    finished: int
    rejected: int
    status_dict: Dict[str, int]
    status_percentages: Dict[str, float]


class CustomerListResponse(BaseModel):
    customers: List[CustomerResponse]
    status_stats: Dict[str, int] = Field(..., description="Status bo'yicha statistika")
    status_dict: Dict[str, int] = Field(..., description="Status soni")
    status_percentages: Dict[str, float] = Field(..., description="Status foizlari")
    status_choices: List[StatusChoice] = Field(..., description="Status tanlovlari")
    permissions: List[str] = Field(..., description="Foydalanuvchi huquqlari")
    selected_status: Optional[str] = Field(None, description="Tanlangan status")
    period_stats: Dict[str, int] = Field(
        ..., description="Bugungi, haftalik, oylik va 3 oylik mijozlar soni"
    )


# --- SEARCH AND FILTER MODELS ---
class CustomerSearchRequest(BaseModel):
    search: Optional[str] = Field(None, description="Qidiruv so'zi")
    status_filter: Optional[CustomerStatus] = Field(None, description="Status bo'yicha filter")
    show_all: bool = Field(False, description="Barcha mijozlarni ko'rsatish")


# --- API TOKEN MODELS (External API uchun) ---
class CustomerAPICreateRequest(BaseModel):
    """Tashqi API orqali mijoz yaratish uchun"""
    full_name: str = Field(..., min_length=1, max_length=255)
    platform: str = Field(..., min_length=1, max_length=255)
    username: Optional[str] = Field(None, max_length=255)
    phone_number: str = Field(..., min_length=1, max_length=20)
    status: CustomerStatus = Field(default=CustomerStatus.need_to_call)
    assistant_name: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None)
    conversation_language: Optional[ConversationLanguageEnum] = Field(
        default=ConversationLanguageEnum.UZ,
        description="Suhbat tili"
    )

    @validator('phone_number')
    def validate_phone_number(cls, v):
        if not v.strip():
            raise ValueError('Telefon raqami bo\'sh bo\'lishi mumkin emas')
        return v.strip()

    @validator('full_name', 'platform')
    def validate_not_empty(cls, v):
        if not v.strip():
            raise ValueError('Bu maydon bo\'sh bo\'lishi mumkin emas')
        return v.strip()

    @validator('username', 'assistant_name')
    def validate_optional_not_empty(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Bu maydon bo\'sh bo\'lishi mumkin emas')
        return v.strip() if v else v

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "full_name": "John Doe",
                "platform": "Telegram",
                "username": "@johndoe",
                "phone_number": "+998901234567",
                "status": "need_to_call",
                "assistant_name": "Assistant Name",
                "notes": "Qo'shimcha ma'lumotlar",
                "conversation_language": "uz"
            }
        }


class CustomerAPIResponse(BaseModel):
    """Tashqi API javob modeli"""
    id: int
    full_name: str
    platform: str
    username: Optional[str]
    phone_number: str
    status: str
    assistant_name: Optional[str]
    notes: Optional[str]
    audio_file_id: Optional[str]
    conversation_language: Optional[str]
    created_at: str
    message: str = "Mijoz muvaffaqiyatli yaratildi"

    class Config:
        from_attributes = True


# --- AUDIO RESPONSE MODEL ---
class AudioResponse(BaseModel):
    """Audio URL javob modeli"""
    audio_url: str
    file_id: str