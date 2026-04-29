import calendar
from collections import defaultdict
from datetime import datetime, date as date_type
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_active_user
from config import ATTENDANCE_API_KEY
from database import get_async_session
from models.user_models import UserRole, attendance_log, user
from schemes.schemes_attendance import AttendanceCreateRequest, AttendanceUpdateRequest


router = APIRouter(prefix="/attendance", tags=["Attendance"])


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
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(require_attendance_api_key),
):
    query = (
        select(
            user.c.id,
            user.c.name,
            user.c.surname,
            user.c.email,
            user.c.role,
            user.c.role_name,
        )
        .where(
            and_(
                user.c.is_active == True,  # noqa: E712
                user.c.role != UserRole.customer,
            )
        )
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )

    if search:
        normalized = f"%{search.strip()}%"
        query = query.where(
            or_(
                user.c.name.ilike(normalized),
                user.c.surname.ilike(normalized),
                user.c.email.ilike(normalized),
            )
        )

    rows = (await session.execute(query)).fetchall()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "surname": row.surname,
                "full_name": f"{row.name} {row.surname}".strip(),
                "email": row.email,
                "role": serialize_role(row.role, row.role_name),
                "role_name": row.role_name,
            }
            for row in rows
        ],
        "total_count": len(rows),
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
        date_from = week_days[0]["date"][5:]
        date_to = week_days[-1]["date"][5:]
        weekly_stats.append({
            "week_number": week_num,
            "week_label": f"{week_num}-hafta ({date_from} – {date_to})",
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
