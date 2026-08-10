from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


ATTENDANCE_STATUSES = {"present", "late", "absent", "incomplete"}
ATTENDANCE_ACTIONS = {"came", "gone"}


def validate_aware_datetime(value: Optional[datetime], field_name: str) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{field_name} timezone bilan yuborilishi kerak")
    return value


class AttendanceCreateRequest(BaseModel):
    employee_id: int
    attendance_date: date
    check_in_time: time
    check_out_time: Optional[time] = None

    @model_validator(mode="after")
    def validate_times(self):
        if self.check_out_time is not None and self.check_out_time < self.check_in_time:
            raise ValueError("check_out_time check_in_time dan oldin bo'lishi mumkin emas")
        return self


class AttendanceUpdateRequest(BaseModel):
    employee_id: Optional[int] = None
    attendance_date: Optional[date] = None
    check_in_time: Optional[time] = None
    check_out_time: Optional[time] = None

    @model_validator(mode="after")
    def validate_times(self):
        if self.check_in_time is not None and self.check_out_time is not None:
            if self.check_out_time < self.check_in_time:
                raise ValueError("check_out_time check_in_time dan oldin bo'lishi mumkin emas")
        return self


class AttendanceUserOption(BaseModel):
    id: int
    name: str
    surname: str
    full_name: str
    email: str
    role: Optional[str] = None
    role_name: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    is_active: bool = True


class AttendanceRecordResponse(BaseModel):
    id: int
    employee_id: int
    full_name: str
    email: str
    role: Optional[str] = None
    role_name: Optional[str] = None
    attendance_date: date
    check_in_time: time
    check_out_time: Optional[time] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AttendanceCreateResponse(BaseModel):
    message: str
    attendance_id: int


class AttendanceDailyRecordRequest(BaseModel):
    source_system: str = "faceid"
    source_session_id: str
    employee_id: int
    attendance_date: date
    check_in_at: Optional[datetime] = None
    check_out_at: Optional[datetime] = None
    check_in_time: Optional[time] = None
    check_out_time: Optional[time] = None
    worked_minutes: Optional[int] = None
    worked_hours_decimal: Optional[float] = None
    status: str = "present"
    shift_id: Optional[str] = None
    shift_name: Optional[str] = None
    is_manual: bool = False
    came_event_id: Optional[str] = None
    gone_event_id: Optional[str] = None
    event_ids: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    source_updated_at: datetime

    @field_validator("check_in_at")
    @classmethod
    def validate_check_in_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return validate_aware_datetime(value, "check_in_at")

    @field_validator("check_out_at")
    @classmethod
    def validate_check_out_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return validate_aware_datetime(value, "check_out_at")

    @field_validator("source_updated_at")
    @classmethod
    def validate_source_updated_at(cls, value: datetime) -> datetime:
        return validate_aware_datetime(value, "source_updated_at")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ATTENDANCE_STATUSES:
            raise ValueError("status faqat present, late, absent yoki incomplete bo'lishi kerak")
        return normalized

    @field_validator("worked_minutes")
    @classmethod
    def validate_worked_minutes(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("worked_minutes manfiy bo'lishi mumkin emas")
        return value

    @field_validator("worked_hours_decimal")
    @classmethod
    def validate_worked_hours_decimal(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value < 0:
            raise ValueError("worked_hours_decimal manfiy bo'lishi mumkin emas")
        return value


class BulkUpsertRequest(BaseModel):
    records: List[AttendanceDailyRecordRequest]


class PatchDailyRecordRequest(BaseModel):
    check_in_at: Optional[datetime] = None
    check_out_at: Optional[datetime] = None
    check_in_time: Optional[time] = None
    check_out_time: Optional[time] = None
    worked_minutes: Optional[int] = None
    worked_hours_decimal: Optional[float] = None
    status: Optional[str] = None
    shift_id: Optional[str] = None
    shift_name: Optional[str] = None
    came_event_id: Optional[str] = None
    gone_event_id: Optional[str] = None
    event_ids: Optional[List[str]] = None
    source_updated_at: Optional[datetime] = None
    is_deleted: Optional[bool] = None
    delete_reason: Optional[str] = None
    note: Optional[str] = None

    @field_validator("check_in_at")
    @classmethod
    def validate_check_in_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return validate_aware_datetime(value, "check_in_at")

    @field_validator("check_out_at")
    @classmethod
    def validate_check_out_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return validate_aware_datetime(value, "check_out_at")

    @field_validator("source_updated_at")
    @classmethod
    def validate_source_updated_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return validate_aware_datetime(value, "source_updated_at")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in ATTENDANCE_STATUSES:
            raise ValueError("status faqat present, late, absent yoki incomplete bo'lishi kerak")
        return normalized

    @field_validator("worked_minutes")
    @classmethod
    def validate_worked_minutes(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("worked_minutes manfiy bo'lishi mumkin emas")
        return value

    @field_validator("worked_hours_decimal")
    @classmethod
    def validate_worked_hours_decimal(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value < 0:
            raise ValueError("worked_hours_decimal manfiy bo'lishi mumkin emas")
        return value


class RawEventItem(BaseModel):
    source_system: str = "faceid"
    source_event_id: str
    employee_id: int
    event_time: datetime
    action: str
    source: str = "auto"
    terminal_ip: Optional[str] = None
    face_confidence: Optional[float] = None
    photo_available: bool = False
    photo_url: Optional[str] = None
    is_manual: bool = False
    manual_created_by: Optional[str] = None
    manual_created_at: Optional[datetime] = None
    manual_comment: Optional[str] = None
    source_created_at: datetime

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: datetime) -> datetime:
        return validate_aware_datetime(value, "event_time")

    @field_validator("manual_created_at")
    @classmethod
    def validate_manual_created_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return validate_aware_datetime(value, "manual_created_at")

    @field_validator("source_created_at")
    @classmethod
    def validate_source_created_at(cls, value: datetime) -> datetime:
        return validate_aware_datetime(value, "source_created_at")

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ATTENDANCE_ACTIONS:
            raise ValueError("action faqat came yoki gone bo'lishi kerak")
        return normalized

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"auto", "manual"}:
            raise ValueError("source faqat auto yoki manual bo'lishi kerak")
        return normalized


class BulkRawEventsRequest(BaseModel):
    events: List[RawEventItem]
