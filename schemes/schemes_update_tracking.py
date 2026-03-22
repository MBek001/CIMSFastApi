from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class WorkdayOverrideType(str, Enum):
    holiday = "holiday"
    short_day = "short_day"


class WorkdayOverrideTargetType(str, Enum):
    all = "all"
    member = "member"


class WorkdayOverrideCreateRequest(BaseModel):
    special_date: date
    day_type: WorkdayOverrideType
    title: str = Field(..., min_length=1, max_length=255)
    note: Optional[str] = Field(default=None, max_length=1000)
    applies_to_all: bool = False
    member_ids: List[int] = Field(default_factory=list)
    workday_hours: Optional[Decimal] = Field(default=None, gt=0, le=24)
    update_required: Optional[bool] = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.applies_to_all and self.member_ids:
            raise ValueError("applies_to_all=true bo'lsa member_ids yuborilmaydi")
        if not self.applies_to_all and not self.member_ids:
            raise ValueError("Kamida bitta member_id yuborilishi kerak")
        if self.day_type == WorkdayOverrideType.short_day and self.workday_hours is None:
            raise ValueError("short_day uchun workday_hours majburiy")
        return self


class WorkdayOverrideUpdateRequest(BaseModel):
    special_date: Optional[date] = None
    day_type: Optional[WorkdayOverrideType] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    note: Optional[str] = Field(default=None, max_length=1000)
    applies_to_all: Optional[bool] = None
    member_id: Optional[int] = None
    workday_hours: Optional[Decimal] = Field(default=None, gt=0, le=24)
    update_required: Optional[bool] = None


class WorkdayOverrideResponse(BaseModel):
    id: int
    special_date: date
    day_type: WorkdayOverrideType
    title: str
    note: Optional[str] = None
    target_type: WorkdayOverrideTargetType
    member_id: Optional[int] = None
    member_name: Optional[str] = None
    workday_hours: Optional[Decimal] = None
    update_required: bool
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WorkdayOverrideBulkResponse(BaseModel):
    message: str
    items: List[WorkdayOverrideResponse]


class WorkdayOverrideMemberOption(BaseModel):
    id: int
    name: str
    surname: str
    full_name: str
    role: Optional[str] = None
    telegram_id: Optional[str] = None

