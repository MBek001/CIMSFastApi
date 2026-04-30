from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


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
    employee_id: int
    attendance_date: date
    check_in_time: Optional[time] = None
    check_out_time: Optional[time] = None
    worked_minutes: Optional[int] = None
    worked_hours_decimal: Optional[float] = None
    status: str = "present"
    shift_name: Optional[str] = None
    source_system: Optional[str] = "faceid"
    source_session_id: Optional[str] = None
    is_manual: bool = False
    note: Optional[str] = None
    source_updated_at: Optional[datetime] = None


class BulkUpsertRequest(BaseModel):
    records: List[AttendanceDailyRecordRequest]


class PatchDailyRecordRequest(BaseModel):
    is_deleted: Optional[bool] = None
    delete_reason: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None


class RawEventItem(BaseModel):
    employee_id: int
    event_time: datetime
    action: str
    source_system: Optional[str] = "faceid"
    terminal_ip: Optional[str] = None
    is_manual: bool = False


class BulkRawEventsRequest(BaseModel):
    events: List[RawEventItem]

