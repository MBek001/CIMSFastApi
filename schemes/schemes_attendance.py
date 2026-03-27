from datetime import date, time
from typing import Optional

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

