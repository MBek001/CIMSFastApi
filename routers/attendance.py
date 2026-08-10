import calendar
from collections import defaultdict
from datetime import datetime, date as date_type
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import String, and_, delete, extract, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_active_user
from config import ATTENDANCE_API_KEY
from database import engine, get_async_session
from models.user_models import UserRole, attendance_daily_record, attendance_log, attendance_raw_event, user
from schemes.schemes_attendance import (
    AttendanceCreateRequest,
    AttendanceUpdateRequest,
    AttendanceDailyRecordRequest,
    BulkUpsertRequest,
    BulkRawEventsRequest,
    PatchDailyRecordRequest,
)


router = APIRouter(prefix="/attendance", tags=["Attendance"])
_attendance_schema_ready = False


async def ensure_attendance_schema() -> None:
    global _attendance_schema_ready
    if _attendance_schema_ready:
        return
    async with engine.begin() as conn:
        await conn.execute(text("""ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS check_in_at TIMESTAMPTZ NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS check_out_at TIMESTAMPTZ NULL"""))
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'attendance_daily_record'
                      AND column_name = 'source_updated_at'
                      AND data_type = 'timestamp without time zone'
                ) THEN
                    ALTER TABLE attendance_daily_record
                    ALTER COLUMN source_updated_at TYPE TIMESTAMPTZ
                    USING source_updated_at AT TIME ZONE 'Asia/Tashkent';
                END IF;
            END $$;
        """))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS shift_id VARCHAR(100) NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS came_event_id VARCHAR(100) NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS gone_event_id VARCHAR(100) NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS event_ids JSONB NOT NULL DEFAULT '[]'::jsonb"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ALTER COLUMN source_system SET DEFAULT 'faceid'"""))
        await conn.execute(text("""UPDATE attendance_daily_record SET source_system = 'faceid' WHERE source_system IS NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ALTER COLUMN source_system SET NOT NULL"""))
        await conn.execute(text("""UPDATE attendance_daily_record SET source_session_id = CONCAT('legacy:', id) WHERE source_session_id IS NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ALTER COLUMN source_session_id SET NOT NULL"""))
        await conn.execute(text("""UPDATE attendance_daily_record SET source_updated_at = COALESCE(updated_at, created_at, NOW()) WHERE source_updated_at IS NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_daily_record ALTER COLUMN source_updated_at SET NOT NULL"""))
        await conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_daily_record_source_session_idx ON attendance_daily_record(source_system, source_session_id)"""))
        await conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_daily_record_source_employee_date_idx ON attendance_daily_record(source_system, employee_id, attendance_date)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_attendance_daily_record_source_system ON attendance_daily_record(source_system)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_attendance_daily_record_status ON attendance_daily_record(status)"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(100) NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'auto'"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS face_confidence NUMERIC(5,4) NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS photo_available BOOLEAN NOT NULL DEFAULT FALSE"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS photo_url TEXT NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS manual_created_by VARCHAR(150) NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS manual_created_at TIMESTAMPTZ NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS manual_comment TEXT NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS source_created_at TIMESTAMPTZ NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW()"""))
        await conn.execute(text("""UPDATE attendance_raw_event SET source_system = 'faceid' WHERE source_system IS NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ALTER COLUMN source_system SET DEFAULT 'faceid'"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ALTER COLUMN source_system SET NOT NULL"""))
        await conn.execute(text("""UPDATE attendance_raw_event SET source_event_id = CONCAT('legacy:', id) WHERE source_event_id IS NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ALTER COLUMN source_event_id SET NOT NULL"""))
        await conn.execute(text("""UPDATE attendance_raw_event SET source_created_at = COALESCE(source_created_at, created_at, NOW()) WHERE source_created_at IS NULL"""))
        await conn.execute(text("""ALTER TABLE attendance_raw_event ALTER COLUMN source_created_at SET NOT NULL"""))
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'attendance_raw_event'
                      AND column_name = 'event_time'
                      AND data_type = 'timestamp without time zone'
                ) THEN
                    ALTER TABLE attendance_raw_event
                    ALTER COLUMN event_time TYPE TIMESTAMPTZ
                    USING event_time AT TIME ZONE 'Asia/Tashkent';
                END IF;
            END $$;
        """))
        await conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_raw_event_source_event_idx ON attendance_raw_event(source_system, source_event_id)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_attendance_raw_event_source_system ON attendance_raw_event(source_system)"""))
    _attendance_schema_ready = True


@router.on_event("startup")
async def warm_attendance_schema() -> None:
    await ensure_attendance_schema()


def serialize_role(role_value, role_name: Optional[str]) -> Optional[str]:
    if getattr(role_value, "value", None):
        return str(role_value.value)
    if role_value:
        return str(role_value)
    if role_name:
        return str(role_name)
    return None


def require_attendance_api_key(x_attendance_key: Optional[str] = Header(default=None)) -> None:
    if not ATTENDANCE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ATTENDANCE_API_KEY serverda sozlanmagan",
        )
    if x_attendance_key != ATTENDANCE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Attendance key noto'g'ri yoki yuborilmagan",
        )


async def ensure_employee_exists(session: AsyncSession, employee_id: int):
    result = await session.execute(
        select(
            user.c.id,
            user.c.name,
            user.c.surname,
            user.c.email,
            user.c.role,
            user.c.role_name,
            user.c.is_active,
        ).where(
            and_(
                user.c.id == employee_id,
                user.c.is_active == True,  # noqa: E712
                user.c.role != UserRole.customer,
            )
        )
    )
    employee_row = result.fetchone()
    if not employee_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee topilmadi")
    return employee_row


async def ensure_unique_attendance(
    session: AsyncSession,
    employee_id: int,
    attendance_date: date_type,
    exclude_id: Optional[int] = None,
) -> None:
    query = select(attendance_log.c.id).where(
        and_(
            attendance_log.c.employee_id == employee_id,
            attendance_log.c.attendance_date == attendance_date,
        )
    )
    if exclude_id is not None:
        query = query.where(attendance_log.c.id != exclude_id)
    result = await session.execute(query)
    if result.scalar() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bu employee uchun shu sanada attendance allaqachon mavjud",
        )


@router.get("/users", summary="Attendance uchun userlar ro'yxati")
async def list_attendance_users(
    search: Optional[str] = Query(default=None, description="Ism, familiya yoki email bo'yicha qidirish"),
    is_active: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_attendance_schema()
    query = (
        select(
            user.c.id,
            user.c.name,
            user.c.surname,
            user.c.email,
            user.c.role,
            user.c.role_name,
            user.c.job_title,
            user.c.is_active,
        )
        .where(
            user.c.role != UserRole.customer
        )
        .order_by(user.c.name.asc(), user.c.surname.asc(), user.c.id.asc())
    )

    if is_active is not None:
        query = query.where(user.c.is_active == is_active)
    if search:
        normalized = f"%{search.strip()}%"
        query = query.where(
            or_(
                user.c.name.ilike(normalized),
                user.c.surname.ilike(normalized),
                user.c.email.ilike(normalized),
                user.c.id.cast(String).ilike(normalized),
            )
        )

    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total_count = int((await session.execute(count_query)).scalar() or 0)
    rows = (await session.execute(query.offset((page - 1) * page_size).limit(page_size))).fetchall()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "surname": row.surname,
                "full_name": f"{row.name} {row.surname}".strip(),
                "email": row.email,
                "department": None,
                "position": row.job_title,
                "role": serialize_role(row.role, row.role_name),
                "role_name": row.role_name,
                "is_active": row.is_active,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
    }


@router.get("/records", summary="Attendance recordlar ro'yxati")
async def list_attendance_records(
    employee_id: Optional[int] = None,
    start_date: Optional[date_type] = None,
    end_date: Optional[date_type] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date start_date dan oldin bo'lishi mumkin emas")

    query = (
        select(
            attendance_log.c.id,
            attendance_log.c.employee_id,
            attendance_log.c.attendance_date,
            attendance_log.c.check_in_time,
            attendance_log.c.check_out_time,
            attendance_log.c.created_by,
            attendance_log.c.created_at,
            attendance_log.c.updated_at,
            user.c.name,
            user.c.surname,
            user.c.email,
            user.c.role,
            user.c.role_name,
        )
        .select_from(attendance_log.join(user, attendance_log.c.employee_id == user.c.id))
        .order_by(attendance_log.c.attendance_date.desc(), attendance_log.c.id.desc())
    )

    conditions = []
    if employee_id is not None:
        conditions.append(attendance_log.c.employee_id == employee_id)
    if start_date is not None:
        conditions.append(attendance_log.c.attendance_date >= start_date)
    if end_date is not None:
        conditions.append(attendance_log.c.attendance_date <= end_date)
    if conditions:
        query = query.where(and_(*conditions))

    rows = (await session.execute(query)).fetchall()
    return {
        "items": [
            {
                "id": row.id,
                "employee_id": row.employee_id,
                "full_name": f"{row.name} {row.surname}".strip(),
                "email": row.email,
                "role": serialize_role(row.role, row.role_name),
                "role_name": row.role_name,
                "attendance_date": row.attendance_date.isoformat(),
                "check_in_time": row.check_in_time.isoformat() if row.check_in_time else None,
                "check_out_time": row.check_out_time.isoformat() if row.check_out_time else None,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ],
        "total_count": len(rows),
    }


@router.post("/records", summary="Attendance record yaratish")
async def create_attendance_record(
    payload: AttendanceCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_employee_exists(session, payload.employee_id)
    await ensure_unique_attendance(session, payload.employee_id, payload.attendance_date)

    result = await session.execute(
        insert(attendance_log)
        .values(
            employee_id=payload.employee_id,
            attendance_date=payload.attendance_date,
            check_in_time=payload.check_in_time,
            check_out_time=payload.check_out_time,
            created_by=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        .returning(attendance_log.c.id)
    )
    attendance_id = result.scalar_one()
    await session.commit()
    return {"message": "Attendance record saqlandi", "attendance_id": attendance_id}


@router.put("/records/{attendance_id}", summary="Attendance record yangilash")
async def update_attendance_record(
    attendance_id: int,
    payload: AttendanceUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    existing_result = await session.execute(select(attendance_log).where(attendance_log.c.id == attendance_id))
    existing_row = existing_result.fetchone()
    if not existing_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attendance record topilmadi")

    update_data = payload.model_dump(exclude_unset=True)
    final_employee_id = update_data.get("employee_id", existing_row.employee_id)
    final_attendance_date = update_data.get("attendance_date", existing_row.attendance_date)
    final_check_in_time = update_data.get("check_in_time", existing_row.check_in_time)
    final_check_out_time = update_data.get("check_out_time", existing_row.check_out_time)

    if final_check_out_time is not None and final_check_out_time < final_check_in_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="check_out_time check_in_time dan oldin bo'lishi mumkin emas",
        )

    await ensure_employee_exists(session, final_employee_id)
    await ensure_unique_attendance(session, final_employee_id, final_attendance_date, exclude_id=attendance_id)

    update_data["updated_at"] = datetime.utcnow()
    await session.execute(update(attendance_log).where(attendance_log.c.id == attendance_id).values(**update_data))
    await session.commit()
    return {"message": "Attendance record yangilandi", "attendance_id": attendance_id}


@router.delete("/records/{attendance_id}", summary="Attendance record o'chirish")
async def delete_attendance_record(
    attendance_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    result = await session.execute(delete(attendance_log).where(attendance_log.c.id == attendance_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attendance record topilmadi")
    return {"message": "Attendance record o'chirildi", "attendance_id": attendance_id}


# ---------------------------------------------------------------------------
# Office time helpers
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES = {
    0: "Dushanba", 1: "Seshanba", 2: "Chorshanba",
    3: "Payshanba", 4: "Juma", 5: "Shanba", 6: "Yakshanba",
}


def _calc_duration_minutes(check_in, check_out) -> Optional[int]:
    if check_in is None or check_out is None:
        return None
    diff = (check_out.hour * 60 + check_out.minute) - (check_in.hour * 60 + check_in.minute)
    return diff if diff >= 0 else None


def _build_days(year: int, month: int, att_by_date: dict) -> List[dict]:
    num_days = calendar.monthrange(year, month)[1]
    days = []
    for day in range(1, num_days + 1):
        d = date_type(year, month, day)
        att = att_by_date.get(d, {})
        check_in = att.get("check_in")
        check_out = att.get("check_out")
        duration = _calc_duration_minutes(check_in, check_out)
        days.append({
            "date": str(d),
            "weekday": _WEEKDAY_NAMES[d.weekday()],
            "check_in_time": check_in.isoformat() if check_in else None,
            "check_out_time": check_out.isoformat() if check_out else None,
            "duration_minutes": duration,
            "is_complete": check_in is not None and check_out is not None,
        })
    return days


def _build_weekly_stats(days: List[dict]) -> List[dict]:
    week_buckets: dict = {}
    for day in days:
        d = date_type.fromisoformat(day["date"])
        iso_week = d.isocalendar()[1]
        week_buckets.setdefault(iso_week, []).append(day)

    weekly_stats = []
    for week_num, (_, week_days) in enumerate(sorted(week_buckets.items()), start=1):
        present = [d for d in week_days if d["check_in_time"] is not None]
        durations = [d["duration_minutes"] for d in present if d["duration_minutes"] is not None]
        total_min = sum(durations)
        avg_min = round(total_min / len(durations)) if durations else 0
        date_from = week_days[0]["date"]
        date_to = week_days[-1]["date"]
        weekly_stats.append({
            "week_number": week_num,
            "week_label": f"{week_num}-hafta ({date_from[5:]} – {date_to[5:]})",
            "date_from": date_from,
            "date_to": date_to,
            "days_present": len(present),
            "total_minutes": total_min,
            "avg_daily_minutes": avg_min,
            "total_hours": round(total_min / 60, 1),
        })
    return weekly_stats


def _build_monthly_stats(days: List[dict]) -> dict:
    present = [d for d in days if d["check_in_time"] is not None]
    complete = [d for d in present if d["is_complete"]]
    durations = [d["duration_minutes"] for d in complete if d["duration_minutes"] is not None]
    total_min = sum(durations)
    avg_min = round(total_min / len(durations)) if durations else 0
    return {
        "days_present": len(present),
        "days_complete": len(complete),
        "total_minutes": total_min,
        "avg_daily_minutes": avg_min,
        "total_hours": round(total_min / 60, 1),
    }


def _build_office_time_payload(emp_row, year: int, month: int, att_rows: list) -> dict:
    month_start = date_type(year, month, 1)
    month_end = date_type(year, month, calendar.monthrange(year, month)[1])
    att_by_date = {
        row.attendance_date: {
            "check_in": row.check_in_time,
            "check_out": row.check_out_time,
        }
        for row in att_rows
    }
    days = _build_days(year, month, att_by_date)
    return {
        "employee": {
            "id": emp_row.id,
            "full_name": f"{emp_row.name} {emp_row.surname}".strip(),
            "role": serialize_role(getattr(emp_row, "role", None), getattr(emp_row, "role_name", None)),
        },
        "period": {
            "year": year,
            "month": month,
            "from": str(month_start),
            "to": str(month_end),
        },
        "days": days,
        "weekly_stats": _build_weekly_stats(days),
        "monthly_stats": _build_monthly_stats(days),
    }


def _validate_year_month(year: int, month: int) -> None:
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail="year 2000–2100 oralig'ida bo'lishi kerak")
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month 1–12 oralig'ida bo'lishi kerak")


_MONTH_NAMES_UZ = {
    1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel", 5: "May", 6: "Iyun",
    7: "Iyul", 8: "Avgust", 9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr",
}


def _build_employee_attendance_summary(emp_row, year: int, month: int, att_rows: list) -> dict:
    att_by_date = {
        row.attendance_date: {"check_in": row.check_in_time, "check_out": row.check_out_time}
        for row in att_rows
    }
    days = _build_days(year, month, att_by_date)
    return {
        "employee": {
            "id": emp_row.id,
            "full_name": f"{emp_row.name} {emp_row.surname}".strip(),
            "role": serialize_role(getattr(emp_row, "role", None), getattr(emp_row, "role_name", None)),
        },
        "monthly_stats": _build_monthly_stats(days),
        "weekly_stats": _build_weekly_stats(days),
    }


# ---------------------------------------------------------------------------
# New endpoints
# ---------------------------------------------------------------------------

@router.get("/employee-monthly-office-time", summary="Xodimlar oylik office vaqti (keldi/ketdi)")
async def get_employee_monthly_office_time(
    year: int,
    month: int,
    employee_id: Optional[int] = Query(default=None, description="Berilmasa — barcha aktiv userlar"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    _validate_year_month(year, month)
    month_start = date_type(year, month, 1)
    month_end = date_type(year, month, calendar.monthrange(year, month)[1])

    if employee_id is not None:
        emp_result = await session.execute(
            select(user.c.id, user.c.name, user.c.surname, user.c.role, user.c.role_name)
            .where(and_(user.c.id == employee_id, user.c.is_active == True))  # noqa: E712
        )
        emp_row = emp_result.fetchone()
        if not emp_row:
            raise HTTPException(status_code=404, detail="Xodim topilmadi")

        att_rows = (await session.execute(
            select(
                attendance_log.c.attendance_date,
                attendance_log.c.check_in_time,
                attendance_log.c.check_out_time,
            )
            .where(and_(
                attendance_log.c.employee_id == employee_id,
                attendance_log.c.attendance_date >= month_start,
                attendance_log.c.attendance_date <= month_end,
            ))
            .order_by(attendance_log.c.attendance_date.asc())
        )).fetchall()

        return _build_office_time_payload(emp_row, year, month, att_rows)

    # Barcha aktiv userlar
    user_rows = (await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.role, user.c.role_name)
        .where(and_(user.c.is_active == True, user.c.role != UserRole.customer))  # noqa: E712
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )).fetchall()

    user_ids = [r.id for r in user_rows]
    all_att_rows = (await session.execute(
        select(
            attendance_log.c.employee_id,
            attendance_log.c.attendance_date,
            attendance_log.c.check_in_time,
            attendance_log.c.check_out_time,
        )
        .where(and_(
            attendance_log.c.employee_id.in_(user_ids) if user_ids else False,
            attendance_log.c.attendance_date >= month_start,
            attendance_log.c.attendance_date <= month_end,
        ))
        .order_by(attendance_log.c.employee_id.asc(), attendance_log.c.attendance_date.asc())
    )).fetchall()

    att_by_user: dict = defaultdict(list)
    for row in all_att_rows:
        att_by_user[row.employee_id].append(row)

    items = [
        _build_office_time_payload(emp_row, year, month, att_by_user[emp_row.id])
        for emp_row in user_rows
    ]
    return {"items": items, "total_count": len(items)}


@router.get("/office-time-me", summary="Mening oylik office vaqtim (haftalik breakdown bilan)")
async def get_my_office_time(
    year: int,
    month: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    _validate_year_month(year, month)
    month_start = date_type(year, month, 1)
    month_end = date_type(year, month, calendar.monthrange(year, month)[1])

    att_rows = (await session.execute(
        select(
            attendance_log.c.attendance_date,
            attendance_log.c.check_in_time,
            attendance_log.c.check_out_time,
        )
        .where(and_(
            attendance_log.c.employee_id == current_user.id,
            attendance_log.c.attendance_date >= month_start,
            attendance_log.c.attendance_date <= month_end,
        ))
        .order_by(attendance_log.c.attendance_date.asc())
    )).fetchall()

    return _build_office_time_payload(current_user, year, month, att_rows)


@router.get("/monthly-summary", summary="Barcha xodimlar oylik davomat xulosasi (haftalik breakdown bilan)")
async def get_monthly_attendance_summary(
    year: int,
    month: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    _validate_year_month(year, month)
    month_start = date_type(year, month, 1)
    month_end = date_type(year, month, calendar.monthrange(year, month)[1])

    user_rows = (await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.role, user.c.role_name)
        .where(and_(user.c.is_active == True, user.c.role != UserRole.customer))  # noqa: E712
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )).fetchall()

    user_ids = [r.id for r in user_rows]
    all_att_rows = (await session.execute(
        select(
            attendance_log.c.employee_id,
            attendance_log.c.attendance_date,
            attendance_log.c.check_in_time,
            attendance_log.c.check_out_time,
        )
        .where(and_(
            attendance_log.c.employee_id.in_(user_ids) if user_ids else False,
            attendance_log.c.attendance_date >= month_start,
            attendance_log.c.attendance_date <= month_end,
        ))
        .order_by(attendance_log.c.employee_id.asc(), attendance_log.c.attendance_date.asc())
    )).fetchall()

    att_by_user: dict = defaultdict(list)
    for row in all_att_rows:
        att_by_user[row.employee_id].append(row)

    employees = [
        _build_employee_attendance_summary(emp_row, year, month, att_by_user[emp_row.id])
        for emp_row in user_rows
    ]
    employees.sort(key=lambda x: x["monthly_stats"]["total_hours"], reverse=True)

    employees_with_records = sum(1 for e in employees if e["monthly_stats"]["days_present"] > 0)
    total_hours_all = round(sum(e["monthly_stats"]["total_hours"] for e in employees), 1)
    avg_hours = round(total_hours_all / employees_with_records, 1) if employees_with_records else 0.0

    return {
        "period": {
            "year": year,
            "month": month,
            "month_name": _MONTH_NAMES_UZ[month],
            "date_from": str(month_start),
            "date_to": str(month_end),
        },
        "overall": {
            "total_employees": len(employees),
            "employees_with_records": employees_with_records,
            "total_hours_all": total_hours_all,
            "avg_hours_per_employee": avg_hours,
        },
        "employees": employees,
    }


# ---------------------------------------------------------------------------
# FaceID integration — attendance_daily_record endpoints
# ---------------------------------------------------------------------------

def _serialize_daily_record(row) -> dict:
    return {
        "record_id": row.id,
        "id": row.id,
        "source_system": row.source_system,
        "source_session_id": row.source_session_id,
        "employee_id": row.employee_id,
        "attendance_date": row.attendance_date.isoformat(),
        "check_in_at": row.check_in_at.isoformat() if row.check_in_at else None,
        "check_out_at": row.check_out_at.isoformat() if row.check_out_at else None,
        "check_in_time": row.check_in_time.isoformat() if row.check_in_time else None,
        "check_out_time": row.check_out_time.isoformat() if row.check_out_time else None,
        "worked_minutes": row.worked_minutes,
        "worked_hours_decimal": float(row.worked_hours_decimal) if row.worked_hours_decimal is not None else None,
        "status": row.status,
        "shift_id": row.shift_id,
        "shift_name": row.shift_name,
        "is_manual": row.is_manual,
        "came_event_id": row.came_event_id,
        "gone_event_id": row.gone_event_id,
        "event_ids": row.event_ids or [],
        "note": row.note,
        "source_updated_at": row.source_updated_at.isoformat() if row.source_updated_at else None,
        "is_deleted": row.is_deleted,
        "delete_reason": row.delete_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _upsert_daily_record(session: AsyncSession, payload: AttendanceDailyRecordRequest) -> int:
    now = datetime.utcnow()
    values = {
        "source_system": payload.source_system,
        "source_session_id": payload.source_session_id,
        "employee_id": payload.employee_id,
        "attendance_date": payload.attendance_date,
        "check_in_at": payload.check_in_at,
        "check_out_at": payload.check_out_at,
        "check_in_time": payload.check_in_time,
        "check_out_time": payload.check_out_time,
        "worked_minutes": payload.worked_minutes,
        "worked_hours_decimal": payload.worked_hours_decimal,
        "status": payload.status,
        "shift_id": payload.shift_id,
        "shift_name": payload.shift_name,
        "is_manual": payload.is_manual,
        "came_event_id": payload.came_event_id,
        "gone_event_id": payload.gone_event_id,
        "event_ids": payload.event_ids,
        "note": payload.note,
        "source_updated_at": payload.source_updated_at,
        "is_deleted": False,
        "delete_reason": None,
        "created_at": now,
        "updated_at": now,
    }
    update_cols = {k: v for k, v in values.items() if k != "created_at"}
    stmt = (
        pg_insert(attendance_daily_record)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[
                attendance_daily_record.c.source_system,
                attendance_daily_record.c.employee_id,
                attendance_daily_record.c.attendance_date,
            ],
            set_={**update_cols, "updated_at": now},
        )
        .returning(attendance_daily_record.c.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.put(
    "/daily-records/{employee_id}/{attendance_date}",
    summary="FaceID: Kunlik davomat yozuvi upsert",
)
async def upsert_daily_record(
    employee_id: int,
    attendance_date: date_type,
    payload: AttendanceDailyRecordRequest,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_attendance_schema()
    payload.employee_id = employee_id
    payload.attendance_date = attendance_date
    await ensure_employee_exists(session, employee_id)
    record_id = await _upsert_daily_record(session, payload)
    await session.commit()
    return {
        "success": True,
        "record_id": record_id,
        "employee_id": employee_id,
        "attendance_date": attendance_date.isoformat(),
        "source_session_id": payload.source_session_id,
    }


@router.post(
    "/daily-records/bulk-upsert",
    summary="FaceID: Bir nechta kunlik davomat yozuvlari upsert (partial success)",
)
async def bulk_upsert_daily_records(
    payload: BulkUpsertRequest,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_attendance_schema()
    results = []
    success_count = 0
    failed_count = 0
    for record in payload.records:
        try:
            await ensure_employee_exists(session, record.employee_id)
            record_id = await _upsert_daily_record(session, record)
            await session.commit()
            results.append({
                "success": True,
                "employee_id": record.employee_id,
                "attendance_date": record.attendance_date.isoformat(),
                "source_session_id": record.source_session_id,
                "record_id": record_id,
            })
            success_count += 1
        except Exception as e:
            await session.rollback()
            results.append({
                "success": False,
                "employee_id": record.employee_id,
                "attendance_date": record.attendance_date.isoformat(),
                "source_session_id": record.source_session_id,
                "error": str(e),
            })
            failed_count += 1
    return {"success_count": success_count, "failed_count": failed_count, "results": results}


@router.get(
    "/daily-records/{employee_id}/{attendance_date}",
    summary="FaceID: Bitta kunlik davomat yozuvi",
)
async def get_daily_record(
    employee_id: int,
    attendance_date: date_type,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_attendance_schema()
    result = await session.execute(
        select(attendance_daily_record).where(
            and_(
                attendance_daily_record.c.employee_id == employee_id,
                attendance_daily_record.c.attendance_date == attendance_date,
                attendance_daily_record.c.is_deleted == False,  # noqa: E712
            )
        )
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Davomat yozuvi topilmadi")
    return _serialize_daily_record(row)


@router.patch(
    "/daily-records/{employee_id}/{attendance_date}",
    summary="FaceID: Kunlik davomat yozuvini yangilash (soft delete / patch)",
)
async def patch_daily_record(
    employee_id: int,
    attendance_date: date_type,
    payload: PatchDailyRecordRequest,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_attendance_schema()
    result = await session.execute(
        select(attendance_daily_record.c.id).where(
            and_(
                attendance_daily_record.c.employee_id == employee_id,
                attendance_daily_record.c.attendance_date == attendance_date,
            )
        )
    )
    row_id = result.scalar()
    if row_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Davomat yozuvi topilmadi")

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hech qanday o'zgartirish yo'q")

    update_data["updated_at"] = datetime.utcnow()
    await session.execute(
        update(attendance_daily_record)
        .where(attendance_daily_record.c.id == row_id)
        .values(**update_data)
    )
    await session.commit()
    return {"success": True, "record_id": row_id}


@router.get(
    "/daily-records",
    summary="FaceID: Kunlik davomat yozuvlari ro'yxati",
)
async def list_daily_records(
    employee_id: Optional[int] = Query(default=None),
    date_from: Optional[date_type] = Query(default=None),
    date_to: Optional[date_type] = Query(default=None),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    day: Optional[int] = Query(default=None, ge=1, le=31),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    source_system: Optional[str] = Query(default=None),
    is_manual: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_attendance_schema()
    conditions = [attendance_daily_record.c.is_deleted == False]  # noqa: E712
    if employee_id is not None:
        conditions.append(attendance_daily_record.c.employee_id == employee_id)
    if date_from is not None:
        conditions.append(attendance_daily_record.c.attendance_date >= date_from)
    if date_to is not None:
        conditions.append(attendance_daily_record.c.attendance_date <= date_to)
    if year is not None:
        conditions.append(extract("year", attendance_daily_record.c.attendance_date) == year)
    if month is not None:
        conditions.append(extract("month", attendance_daily_record.c.attendance_date) == month)
    if day is not None:
        conditions.append(extract("day", attendance_daily_record.c.attendance_date) == day)
    if status_filter is not None:
        conditions.append(attendance_daily_record.c.status == status_filter)
    if source_system is not None:
        conditions.append(attendance_daily_record.c.source_system == source_system)
    if is_manual is not None:
        conditions.append(attendance_daily_record.c.is_manual == is_manual)

    total_count = (await session.execute(
        select(func.count()).select_from(attendance_daily_record).where(and_(*conditions))
    )).scalar_one()

    query = (
        select(
            attendance_daily_record,
            user.c.name,
            user.c.surname,
        )
        .select_from(
            attendance_daily_record.join(user, attendance_daily_record.c.employee_id == user.c.id)
        )
        .where(and_(*conditions))
        .order_by(attendance_daily_record.c.attendance_date.desc(), attendance_daily_record.c.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(query)).fetchall()
    return {
        "items": [
            {
                **_serialize_daily_record(row),
                "full_name": f"{row.name} {row.surname}".strip(),
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
    }


@router.post(
    "/raw-events/bulk-upsert",
    summary="FaceID: Bir nechta raw event upsert",
)
@router.post(
    "/raw-events/bulk",
    include_in_schema=False,
)
async def bulk_create_raw_events(
    payload: BulkRawEventsRequest,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    await ensure_attendance_schema()
    if not payload.events:
        return {"success_count": 0, "failed_count": 0, "results": []}

    results = []
    success_count = 0
    failed_count = 0
    now = datetime.utcnow()

    for event in payload.events:
        try:
            await ensure_employee_exists(session, event.employee_id)
            result = await session.execute(
                pg_insert(attendance_raw_event)
                .values(
                    source_system=event.source_system,
                    source_event_id=event.source_event_id,
                    employee_id=event.employee_id,
                    event_time=event.event_time,
                    action=event.action,
                    source=event.source,
                    terminal_ip=event.terminal_ip,
                    face_confidence=event.face_confidence,
                    photo_available=event.photo_available,
                    photo_url=event.photo_url,
                    is_manual=event.is_manual,
                    manual_created_by=event.manual_created_by,
                    manual_created_at=event.manual_created_at,
                    manual_comment=event.manual_comment,
                    source_created_at=event.source_created_at,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[
                        attendance_raw_event.c.source_system,
                        attendance_raw_event.c.source_event_id,
                    ],
                    set_={
                        "employee_id": event.employee_id,
                        "event_time": event.event_time,
                        "action": event.action,
                        "source": event.source,
                        "terminal_ip": event.terminal_ip,
                        "face_confidence": event.face_confidence,
                        "photo_available": event.photo_available,
                        "photo_url": event.photo_url,
                        "is_manual": event.is_manual,
                        "manual_created_by": event.manual_created_by,
                        "manual_created_at": event.manual_created_at,
                        "manual_comment": event.manual_comment,
                        "source_created_at": event.source_created_at,
                        "updated_at": now,
                    },
                )
                .returning(attendance_raw_event.c.id)
            )
            event_id = result.scalar_one()
            await session.commit()
            results.append({
                "success": True,
                "source_event_id": event.source_event_id,
                "employee_id": event.employee_id,
                "event_id": event_id,
            })
            success_count += 1
        except Exception as e:
            await session.rollback()
            results.append({
                "success": False,
                "source_event_id": event.source_event_id,
                "employee_id": event.employee_id,
                "error": str(e),
            })
            failed_count += 1

    return {"success_count": success_count, "failed_count": failed_count, "results": results}
