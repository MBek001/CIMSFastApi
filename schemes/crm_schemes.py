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
    chat_url: Optional[str] = Field(None, max_length=1000, description="Chat URL")
    notes: Optional[str] = Field(None, description="Qo'shimcha eslatmalar")
    recall_time: Optional[datetime] = Field(
        None,
        description="Qayta bog'lanish vaqti (Asia/Tashkent, UTC+5)",
        examples=["2026-03-03T09:53:00+05:00"]
    )
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

    @validator('username', 'assistant_name', 'chat_url')
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
    chat_url: Optional[str] = Field(None, max_length=1000, description="Chat URL")
    notes: Optional[str] = Field(None, description="Qo'shimcha eslatmalar")
    recall_time: Optional[datetime] = Field(
        None,
        description="Qayta bog'lanish vaqti (Asia/Tashkent, UTC+5)",
        examples=["2026-03-03T09:53:00+05:00"]
    )
    conversation_language: Optional[ConversationLanguageEnum] = Field(None, description="Suhbat tili")

    @validator('phone_number', 'full_name', 'platform', 'username', 'assistant_name', 'chat_url')
    def validate_not_empty(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Bu maydon bo\'sh bo\'lishi mumkin emas')
        return v.strip() if v else v


class CustomerDeleteRequest(BaseModel):
    customer_ids: List[int] = Field(..., min_items=1, description="O'chiriladigan mijozlar ID ro'yxati")


class CustomerNoteCreateRequest(BaseModel):
    note: str = Field(..., min_length=1, description="Customer uchun qo'shimcha note")

    @validator("note")
    def validate_note(cls, v):
        if not v.strip():
            raise ValueError("Note bo'sh bo'lishi mumkin emas")
        return v.strip()


class CustomerNoteUpdateRequest(BaseModel):
    note: str = Field(..., min_length=1, description="Yangilangan note matni")

    @validator("note")
    def validate_note(cls, v):
        if not v.strip():
            raise ValueError("Note bo'sh bo'lishi mumkin emas")
        return v.strip()


class CustomerNoteResponse(BaseModel):
    id: int
    customer_id: int
    note: str
    created_by: Optional[int]
    created_by_full_name: Optional[str] = None
    created_at: str
    updated_at: str


class CustomerNoteListResponse(BaseModel):
    customer_id: int
    items: List[CustomerNoteResponse]
    total_count: int


# --- CUSTOMER RESPONSE MODELS ---
class CustomerResponse(BaseModel):
    id: int
    full_name: str
    platform: str
    username: Optional[str]
    phone_number: str
    status: str
    assistant_name: Optional[str]
    chat_url: Optional[str]
    notes: Optional[str]
    aisummary: Optional[str] = None
    audio_file_id: Optional[str]
    audio_url: Optional[str] = None
    recall_time: Optional[str] = None
    conversation_language: Optional[str]
    created_at: str
    is_archived: Optional[bool] = None
    additional_notes: Optional[List[CustomerNoteResponse]] = None

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
    page: int = Field(..., description="Joriy sahifa raqami")
    page_size: int = Field(..., description="Har bir sahifadagi yozuvlar soni")
    total_items: int = Field(..., description="Filterdan keyingi jami yozuvlar soni")
    total_pages: int = Field(..., description="Jami sahifalar soni")
    status_stats: Dict[str, int] = Field(..., description="Status bo'yicha statistika")
    status_dict: Dict[str, int] = Field(..., description="Status soni")
    status_percentages: Dict[str, float] = Field(..., description="Status foizlari")
    status_choices: List[StatusChoice] = Field(..., description="Status tanlovlari")
    permissions: List[str] = Field(..., description="Foydalanuvchi huquqlari")
    selected_status: Optional[str] = Field(None, description="Tanlangan status")
    period_stats: Dict[str, int] = Field(
        ..., description="Bugungi, haftalik, oylik va 3 oylik mijozlar soni"
    )


class CustomerPeriodReportResponse(BaseModel):
    period: str = Field(..., description="Tanlangan davr: 3d, 7d, 15d, 30d yoki custom")
    from_date: str = Field(..., description="Boshlanish sana (YYYY-MM-DD)")
    to_date: str = Field(..., description="Tugash sana (YYYY-MM-DD)")
    total_customers: int = Field(..., description="Tanlangan davrdagi jami customerlar")
    customers: List[CustomerResponse] = Field(..., description="Customerlar ro'yxati")
    status_stats: Dict[str, int] = Field(..., description="Statuslar bo'yicha sonlar")
    status_dict: Dict[str, int] = Field(..., description="Mavjud statuslarning sonlari")
    status_percentages: Dict[str, float] = Field(..., description="Statuslar bo'yicha foizlar")


class CRMPeriodStatusStats(BaseModel):
    total_customers: int = Field(..., description="Davr bo'yicha jami customerlar")
    status_stats: Dict[str, int] = Field(..., description="Statuslar bo'yicha sonlar")
    status_percentages: Dict[str, float] = Field(..., description="Statuslar bo'yicha foizlar")


class CRMPeriodicStatusSummaryResponse(BaseModel):
    generated_at: str = Field(..., description="Statistika generatsiya qilingan vaqt (ISO)")
    today: CRMPeriodStatusStats
    last_3_days: CRMPeriodStatusStats
    last_7_days: CRMPeriodStatusStats
    last_30_days: CRMPeriodStatusStats
    last_90_days: CRMPeriodStatusStats


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
    chat_url: Optional[str] = Field(None, max_length=1000)
    notes: Optional[str] = Field(None)
    recall_time: Optional[datetime] = Field(
        None,
        description="Qayta bog'lanish vaqti (Asia/Tashkent, UTC+5)",
        examples=["2026-03-03T09:53:00+05:00"]
    )
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

    @validator('username', 'assistant_name', 'chat_url')
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
                "chat_url": "https://t.me/example_chat",
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
    chat_url: Optional[str]
    notes: Optional[str]
    aisummary: Optional[str] = None
    audio_file_id: Optional[str]
    recall_time: Optional[str] = None
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
