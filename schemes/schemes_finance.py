from pydantic import BaseModel, validator
from typing import List, Optional
from decimal import Decimal
from datetime import date
from models.admin_models import FinanceType, FinanceStatus, CardType, CurrencyType, TransactionStatus


# --- BASE SCHEMES ---
class FinanceBase(BaseModel):
    type: FinanceType
    status: FinanceStatus
    card: CardType
    service: str
    summ: Decimal
    date: date
    donation_percentage: Decimal = Decimal('0')
    tax_percentage: Optional[Decimal] = Decimal('0')
    transaction_status: TransactionStatus = TransactionStatus.statistical

    @validator('summ')
    def validate_summ(cls, v):
        if v <= 0:
            raise ValueError('Summa 0 dan katta bo\'lishi kerak')
        return v

    @validator('donation_percentage')
    def validate_donation_percentage(cls, v):
        if v < 0 or v > 100:
            raise ValueError('Donation foizi 0 dan 100 gacha bo\'lishi kerak')
        return v

    @validator('tax_percentage')
    def validate_tax_percentage(cls, v):
        if v and (v < 0 or v > 100):
            raise ValueError('Tax foizi 0 dan 100 gacha bo\'lishi kerak')
        return v


# --- REQUEST SCHEMES ---
class FinanceCreateRequest(FinanceBase):
    """Finance yaratish uchun request scheme"""
    pass


class FinanceUpdateRequest(FinanceBase):
    """Finance yangilash uchun request scheme"""
    pass


class TransferRequest(BaseModel):
    """Kartalar o'rtasida transfer uchun request scheme"""
    from_card: CardType
    to_card: CardType
    amount: Decimal
    tax_percentage: Decimal = Decimal('0')

    @validator('amount')
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Transfer summasi 0 dan katta bo\'lishi kerak')
        return v

    @validator('tax_percentage')
    def validate_tax_percentage(cls, v):
        if v < 0 or v > 100:
            raise ValueError('Tax foizi 0 dan 100 gacha bo\'lishi kerak')
        return v


# --- RESPONSE SCHEMES ---
class FinanceResponse(BaseModel):
    """Bitta finance yozuvi uchun response scheme"""
    id: int
    type: str
    status: str
    card: str
    card_display: str
    service: str
    summ: float
    currency: str
    date: str
    donation: float
    donation_percentage: float
    tax_percentage: float
    exchange_rate: float
    transaction_status: str
    initial_date: Optional[str] = None

    class Config:
        from_attributes = True


class BalanceInfo(BaseModel):
    """Balans ma'lumotlari uchun scheme"""
    card1_balance: str  # Company Account UZB (UZS)
    card2_balance: str  # Uzcard UZB (UZS)
    card3_balance: str  # Company Account US (USD)
    total_balance: str  # Umumiy balans (UZS da)
    potential_balance: str  # Potensial balans (UZS da)


class MemberCardInfo(BaseModel):
    """Member karta ma'lumotlari"""
    card_number: str
    is_primary: bool


class MemberData(BaseModel):
    """Member ma'lumotlari"""
    name: str
    surname: str
    cards: List[MemberCardInfo]


class DashboardResponse(BaseModel):
    """Finance dashboard response scheme"""
    finances: List[FinanceResponse]
    permissions: List[str]
    donation_balance: float
    exchange_rate: str
    balances: BalanceInfo
    member_data: List[MemberData]

    class Config:
        from_attributes = True


class FinanceListResponse(BaseModel):
    """Finance yozuvlari ro'yxati uchun response scheme"""
    finances: List[FinanceResponse]
    total_count: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool

    class Config:
        from_attributes = True


class ExchangeRateResponse(BaseModel):
    """Valyuta kursi response scheme"""
    usd_to_uzs: float
    formatted_rate: str

    class Config:
        from_attributes = True


class DonationResetResponse(BaseModel):
    """Donation reset response scheme"""
    success: bool
    message: str
    new_balance: float

    class Config:
        from_attributes = True


# --- COMMON RESPONSE SCHEMES ---
class SuccessResponse(BaseModel):
    """Muvaffaqiyatli amal uchun response"""
    message: str

    class Config:
        from_attributes = True


class CreateResponse(BaseModel):
    """Yaratish amali uchun response"""
    message: str
    id: int

    class Config:
        from_attributes = True


class ErrorResponse(BaseModel):
    """Xatolik uchun response"""
    detail: str
    status_code: int

    class Config:
        from_attributes = True


# --- FILTER SCHEMES (KELAJAKDA FOYDALANISH UCHUN) ---
class FinanceFilterRequest(BaseModel):
    """Finance ro'yxatini filter qilish uchun"""
    type: Optional[FinanceType] = None
    status: Optional[FinanceStatus] = None
    card: Optional[CardType] = None
    currency: Optional[CurrencyType] = None
    transaction_status: Optional[TransactionStatus] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    service_search: Optional[str] = None

    class Config:
        from_attributes = True


class FinanceStatsResponse(BaseModel):
    """Finance statistikasi uchun response"""
    total_income: float
    total_outcome: float
    total_donation: float
    net_profit: float
    transaction_count: int
    income_count: int
    outcome_count: int

    class Config:
        from_attributes = True


# --- MONTHLY REPORT SCHEMES ---
class MonthlyFinanceReport(BaseModel):
    """Oylik finance hisoboti"""
    month: int
    year: int
    total_income: float
    total_outcome: float
    net_amount: float
    donation_amount: float
    transaction_count: int

    class Config:
        from_attributes = True


class YearlyFinanceReport(BaseModel):
    """Yillik finance hisoboti"""
    year: int
    monthly_reports: List[MonthlyFinanceReport]
    yearly_total_income: float
    yearly_total_outcome: float
    yearly_net_amount: float
    yearly_donation_amount: float
    yearly_transaction_count: int

    class Config:
        from_attributes = True