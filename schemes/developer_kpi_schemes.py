from datetime import date, datetime, time
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DeveloperWorkScheduleRequest(BaseModel):
    user_id: int
    weekday: int = Field(ge=0, le=6)
    work_start_time: time
    work_end_time: time
    free_start_time: Optional[time] = None
    free_end_time: Optional[time] = None
    late_grace_minutes: int = Field(default=0, ge=0, le=240)
    is_active: bool = True


class DeveloperKpiFeatureCreateRequest(BaseModel):
    project_id: int
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    points: int = Field(ge=1, le=13)
    owner_id: int
    frontend_percent: int = Field(default=0, ge=0, le=100)
    backend_percent: int = Field(default=100, ge=0, le=100)
    due_date: date
    status: str = Field(default="planned", max_length=40)
    is_mandatory: bool = True
    lock_now: bool = False

    @field_validator("title", "status")
    @classmethod
    def strip_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("bo'sh bo'lishi mumkin emas")
        return normalized


class DeveloperKpiFeatureUpdateRequest(BaseModel):
    project_id: Optional[int] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    points: Optional[int] = Field(default=None, ge=1, le=13)
    owner_id: Optional[int] = None
    frontend_percent: Optional[int] = Field(default=None, ge=0, le=100)
    backend_percent: Optional[int] = Field(default=None, ge=0, le=100)
    due_date: Optional[date] = None
    status: Optional[str] = Field(default=None, max_length=40)
    is_mandatory: Optional[bool] = None
    is_locked: Optional[bool] = None


class DeveloperKpiFeatureAcceptRequest(BaseModel):
    accepted_at: Optional[datetime] = None


class DeveloperKpiBlockedPeriodRequest(BaseModel):
    project_id: int
    feature_id: Optional[int] = None
    employee_id: int
    started_at: datetime
    ended_at: Optional[datetime] = None
    reason: str = Field(min_length=1)
    dependency: Optional[str] = None
    evidence_url: Optional[str] = None
    is_external: bool = True


class DeveloperKpiBlockedPeriodUpdateRequest(BaseModel):
    project_id: Optional[int] = None
    feature_id: Optional[int] = None
    employee_id: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    reason: Optional[str] = None
    dependency: Optional[str] = None
    evidence_url: Optional[str] = None
    is_external: Optional[bool] = None
    approval_status: Optional[str] = None


class DeveloperKpiQualityEventRequest(BaseModel):
    project_id: int
    feature_id: Optional[int] = None
    card_id: Optional[int] = None
    employee_id: int
    severity: str = Field(min_length=1, max_length=40)
    source: str = Field(default="manual", max_length=40)
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    event_date: date
    confirmed: bool = True
    is_duplicate: bool = False
    external_cause: bool = False


class DeveloperKpiQualityEventUpdateRequest(BaseModel):
    project_id: Optional[int] = None
    feature_id: Optional[int] = None
    card_id: Optional[int] = None
    employee_id: Optional[int] = None
    severity: Optional[str] = Field(default=None, max_length=40)
    source: Optional[str] = Field(default=None, max_length=40)
    title: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    event_date: Optional[date] = None
    confirmed: Optional[bool] = None
    is_duplicate: Optional[bool] = None
    external_cause: Optional[bool] = None


class DeveloperKpiDeductionUpdateRequest(BaseModel):
    status: str = Field(pattern="^(candidate|approved|rejected)$")
    reason: Optional[str] = None


class DeveloperProjectDeliveryRequest(BaseModel):
    actual_delivery_date: Optional[date] = None
    delivery_status: Optional[str] = Field(default=None, max_length=50)
    approved_blocked_days: int = Field(default=0, ge=0, le=365)
