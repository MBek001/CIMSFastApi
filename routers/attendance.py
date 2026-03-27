from datetime import datetime, date as date_type
from typing import Optional

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
