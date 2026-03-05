
from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete, func, and_
from models.user_models import user_page_permission, monthly_update, user, monthly_penalty
from database import get_async_session
from auth_utils.auth_func import get_current_active_user


router = APIRouter(prefix="/members", tags=['Employess Api'])

MONTH_FILTER_ALIASES = {
    1: {"1", "01", "janvar", "yanvar", "january", "jan"},
    2: {"2", "02", "fevral", "february", "feb"},
    3: {"3", "03", "mart", "march", "mar"},
    4: {"4", "04", "aprel", "april", "apr"},
    5: {"5", "05", "may"},
    6: {"6", "06", "iyun", "june", "jun"},
    7: {"7", "07", "iyul", "july", "jul"},
    8: {"8", "08", "avgust", "august", "aug"},
    9: {"9", "09", "sentabr", "september", "sep"},
    10: {"10", "oktabr", "october", "oct"},
    11: {"11", "noyabr", "november", "nov"},
    12: {"12", "dekabr", "december", "dec"},
}


def parse_employee_ids(employee_ids: Optional[str]) -> List[int]:
    if not employee_ids:
        return []

    parsed_ids = []
    for raw_id in employee_ids.split(","):
        value = raw_id.strip()
        if not value:
            continue
        if not value.isdigit():
            raise HTTPException(
                status_code=400,
                detail="employee_ids noto'g'ri formatda. Misol: 1,2,3"
            )
        parsed_ids.append(int(value))

    return list(dict.fromkeys(parsed_ids))


def as_money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_salary_estimate(base_salary: Decimal, total_penalty_points: Decimal) -> dict:
    clamped_penalty = min(max(total_penalty_points, Decimal("0")), Decimal("100"))
    deduction_amount = (base_salary * clamped_penalty / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    estimated_salary = (base_salary - deduction_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "base_salary": as_money(base_salary),
        "total_penalty_points": as_money(total_penalty_points),
        "penalty_percentage": as_money(clamped_penalty),
        "deduction_amount": as_money(deduction_amount),
        "estimated_salary": as_money(max(estimated_salary, Decimal("0")))
    }


@router.post("/member/penalties/add", summary="Employee uchun oylik jarima ball qo'shish")
async def add_member_penalty(
    user_id: int,
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    penalty_points: float = Query(..., gt=0, le=100),
    reason: Optional[str] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yo‘q")

    user_result = await session.execute(
        select(user.c.id).where(user.c.id == user_id)
    )
    if not user_result.fetchone():
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    result = await session.execute(
        insert(monthly_penalty)
        .values(
            user_id=user_id,
            year=year,
            month=month,
            penalty_points=Decimal(str(penalty_points)),
            reason=reason,
            created_by=current_user.id,
            created_at=datetime.now()
        )
        .returning(monthly_penalty.c.id)
    )
    penalty_id = result.scalar()
    await session.commit()

    return {
        "message": "Jarima ball muvaffaqiyatli qo‘shildi",
        "penalty_id": penalty_id,
        "user_id": user_id,
        "year": year,
        "month": month,
        "penalty_points": penalty_points
    }


@router.get("/member/salary-estimate", summary="Tanlangan oy uchun taxminiy oylik hisoblash")
async def get_member_salary_estimate(
    user_id: int,
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yo‘q")

    user_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.default_salary).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()
    if not user_data:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    penalties_result = await session.execute(
        select(
            monthly_penalty.c.id,
            monthly_penalty.c.penalty_points,
            monthly_penalty.c.reason,
            monthly_penalty.c.created_at
        )
        .where(
            and_(
                monthly_penalty.c.user_id == user_id,
                monthly_penalty.c.year == year,
                monthly_penalty.c.month == month
            )
        )
        .order_by(monthly_penalty.c.created_at.asc())
    )
    penalty_rows = penalties_result.fetchall()

    total_penalty_points = Decimal("0")
    for row in penalty_rows:
        total_penalty_points += Decimal(str(row.penalty_points or 0))

    base_salary = Decimal(str(user_data.default_salary or 0))
    estimate = calculate_salary_estimate(base_salary, total_penalty_points)

    return {
        "user": {
            "id": user_data.id,
            "full_name": f"{user_data.name} {user_data.surname}"
        },
        "period": {
            "year": year,
            "month": month
        },
        "penalties_count": len(penalty_rows),
        "penalties": [
            {
                "id": row.id,
                "penalty_points": float(row.penalty_points or 0),
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if row.created_at else None
            }
            for row in penalty_rows
        ],
        "salary_estimate": estimate
    }


@router.get("/member/salary-estimates", summary="Employee'lar bo'yicha taxminiy oyliklar (oylik)")
async def get_members_salary_estimates(
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    employee_ids: Optional[str] = Query(default=None, description="Comma-separated user IDs. Masalan: 1,2,3"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yo‘q")

    selected_employee_ids = parse_employee_ids(employee_ids)

    users_query = select(
        user.c.id,
        user.c.name,
        user.c.surname,
        user.c.default_salary
    )
    if selected_employee_ids:
        users_query = users_query.where(user.c.id.in_(selected_employee_ids))
    users_query = users_query.order_by(user.c.name, user.c.surname)

    users_result = await session.execute(users_query)
    users_rows = users_result.fetchall()

    penalties_query = select(
        monthly_penalty.c.user_id,
        func.coalesce(func.sum(monthly_penalty.c.penalty_points), 0).label("total_penalty_points"),
        func.count(monthly_penalty.c.id).label("penalties_count")
    ).where(
        and_(
            monthly_penalty.c.year == year,
            monthly_penalty.c.month == month
        )
    ).group_by(monthly_penalty.c.user_id)

    if selected_employee_ids:
        penalties_query = penalties_query.where(monthly_penalty.c.user_id.in_(selected_employee_ids))

    penalties_result = await session.execute(penalties_query)
    penalties_map = {row.user_id: row for row in penalties_result.fetchall()}

    employees = []
    total_base_salary = Decimal("0")
    total_estimated_salary = Decimal("0")
    total_deduction = Decimal("0")

    for row in users_rows:
        base_salary = Decimal(str(row.default_salary or 0))
        penalty_row = penalties_map.get(row.id)
        total_penalty_points = Decimal(str(penalty_row.total_penalty_points if penalty_row else 0))
        penalties_count = int(penalty_row.penalties_count) if penalty_row else 0

        estimate = calculate_salary_estimate(base_salary, total_penalty_points)
        total_base_salary += Decimal(str(estimate["base_salary"]))
        total_estimated_salary += Decimal(str(estimate["estimated_salary"]))
        total_deduction += Decimal(str(estimate["deduction_amount"]))

        employees.append({
            "user_id": row.id,
            "full_name": f"{row.name} {row.surname}",
            "penalties_count": penalties_count,
            "salary_estimate": estimate
        })

    return {
        "period": {
            "year": year,
            "month": month
        },
        "filters": {
            "employee_ids": selected_employee_ids
        },
        "summary": {
            "employees_count": len(employees),
            "total_base_salary": as_money(total_base_salary),
            "total_deduction_amount": as_money(total_deduction),
            "total_estimated_salary": as_money(total_estimated_salary)
        },
        "employees": employees
    }


@router.get("/member/updates/statistics", summary="Employee oylik update statistikasi (filter bilan)")
async def get_employee_monthly_update_statistics(
    year: Optional[int] = Query(default=None, ge=2020, le=2035),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    employee_ids: Optional[str] = Query(default=None, description="Comma-separated user IDs. Masalan: 1,2,3"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yo‘q")

    selected_employee_ids = parse_employee_ids(employee_ids)

    filters = []
    if year is not None:
        filters.append(monthly_update.c.year == year)
    if month is not None:
        month_values = {item.lower() for item in MONTH_FILTER_ALIASES.get(month, set())}
        filters.append(func.lower(func.trim(monthly_update.c.month)).in_(month_values))
    if selected_employee_ids:
        filters.append(monthly_update.c.user_id.in_(selected_employee_ids))

    employees_query = (
        select(
            monthly_update.c.user_id.label("user_id"),
            user.c.name.label("name"),
            user.c.surname.label("surname"),
            func.count(monthly_update.c.id).label("reports_count"),
            func.avg(monthly_update.c.update_percentage).label("avg_update_percentage"),
            func.min(monthly_update.c.update_percentage).label("min_update_percentage"),
            func.max(monthly_update.c.update_percentage).label("max_update_percentage"),
            func.sum(monthly_update.c.salary_amount).label("total_salary_amount"),
            func.max(monthly_update.c.update_date).label("latest_report_date")
        )
        .select_from(monthly_update.join(user, monthly_update.c.user_id == user.c.id))
        .group_by(monthly_update.c.user_id, user.c.name, user.c.surname)
        .order_by(user.c.name, user.c.surname)
    )
    if filters:
        employees_query = employees_query.where(and_(*filters))

    employees_result = await session.execute(employees_query)
    employee_rows = employees_result.fetchall()

    salary_estimate_map = {}
    salary_summary = None
    if year is not None and month is not None and employee_rows:
        employee_id_list = [row.user_id for row in employee_rows]

        salary_result = await session.execute(
            select(user.c.id, user.c.default_salary)
            .where(user.c.id.in_(employee_id_list))
        )
        salary_base_map = {row.id: Decimal(str(row.default_salary or 0)) for row in salary_result.fetchall()}

        penalty_result = await session.execute(
            select(
                monthly_penalty.c.user_id,
                func.coalesce(func.sum(monthly_penalty.c.penalty_points), 0).label("total_penalty_points"),
                func.count(monthly_penalty.c.id).label("penalties_count")
            )
            .where(
                and_(
                    monthly_penalty.c.user_id.in_(employee_id_list),
                    monthly_penalty.c.year == year,
                    monthly_penalty.c.month == month
                )
            )
            .group_by(monthly_penalty.c.user_id)
        )
        penalty_map = {row.user_id: row for row in penalty_result.fetchall()}

        total_base_salary = Decimal("0")
        total_deduction_amount = Decimal("0")
        total_estimated_salary = Decimal("0")

        for employee_id in employee_id_list:
            base_salary = salary_base_map.get(employee_id, Decimal("0"))
            penalty_row = penalty_map.get(employee_id)
            total_penalty_points = Decimal(str(penalty_row.total_penalty_points if penalty_row else 0))
            penalties_count = int(penalty_row.penalties_count) if penalty_row else 0

            estimate = calculate_salary_estimate(base_salary, total_penalty_points)
            estimate["penalties_count"] = penalties_count
            salary_estimate_map[employee_id] = estimate

            total_base_salary += Decimal(str(estimate["base_salary"]))
            total_deduction_amount += Decimal(str(estimate["deduction_amount"]))
            total_estimated_salary += Decimal(str(estimate["estimated_salary"]))

        salary_summary = {
            "total_base_salary": as_money(total_base_salary),
            "total_deduction_amount": as_money(total_deduction_amount),
            "total_estimated_salary": as_money(total_estimated_salary)
        }

    summary_query = select(
        func.count(monthly_update.c.id).label("total_reports"),
        func.count(func.distinct(monthly_update.c.user_id)).label("total_employees"),
        func.avg(monthly_update.c.update_percentage).label("average_update_percentage"),
        func.sum(monthly_update.c.salary_amount).label("total_salary_amount")
    )
    if filters:
        summary_query = summary_query.where(and_(*filters))

    summary_result = await session.execute(summary_query)
    summary = summary_result.fetchone()

    return {
        "filters": {
            "year": year,
            "month": month,
            "employee_ids": selected_employee_ids
        },
        "summary": {
            "total_employees": summary.total_employees or 0,
            "total_reports": summary.total_reports or 0,
            "average_update_percentage": round(float(summary.average_update_percentage or 0), 2),
            "total_salary_amount": round(float(summary.total_salary_amount or 0), 2),
            "salary_estimate_summary": salary_summary
        },
        "employees": [
            {
                "user_id": row.user_id,
                "full_name": f"{row.name} {row.surname}",
                "reports_count": row.reports_count,
                "average_update_percentage": round(float(row.avg_update_percentage or 0), 2),
                "min_update_percentage": round(float(row.min_update_percentage or 0), 2),
                "max_update_percentage": round(float(row.max_update_percentage or 0), 2),
                "total_salary_amount": round(float(row.total_salary_amount or 0), 2),
                "latest_report_date": str(row.latest_report_date) if row.latest_report_date else None,
                "salary_estimate": salary_estimate_map.get(row.user_id)
            }
            for row in employee_rows
        ]
    }


# 🔹 1. CREATE — Yangi oy ma’lumotini kiritish (faqat update_list permission bilan)
@router.post("/member/update", summary="Member uchun yangi oylik ma'lumot kiritish")
async def add_member_update(
    user_id: int,
    year: int,
    month: str,
    update_percentage: float,
    salary_amount: float,
    next_payment_date: date,
    note:str,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):

    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    new_update = {
        "user_id": user_id,
        "year": year,
        "month": month,
        "update_date": date.today(),
        "update_percentage": update_percentage,
        "salary_amount": salary_amount,
        "next_payment_date": next_payment_date,
        "note": note,
    }

    await session.execute(insert(monthly_update).values(**new_update))
    await session.commit()
    return {"message": f"{month}/{year} uchun update muvaffaqiyatli qo‘shildi"}


# 🔹 2. GET — Hamma foydalanuvchilar uchun barcha update’lar (faqat update_list sahifasiga ruxsati borlar uchun)
@router.get("/member/updates/all", summary="Barcha foydalanuvchilarning update'larini olish (ruxsat bilan)")
async def get_all_updates(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    # 🔐 "update_list" sahifasiga ruxsati borligini tekshirish
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yo‘q")

    # 🔍 Barcha foydalanuvchilarning update’larini olish
    result = await session.execute(select(monthly_update))
    updates = result.fetchall()

    if not updates:
        return {"message": "Hech qanday update topilmadi", "data": []}

    return [
        {
            "id": u.id,
            "user_id": u.user_id,
            "year": u.year,
            "month": u.month,
            "update_date": u.update_date,
            "update_percentage": float(u.update_percentage),
            "salary_amount": float(u.salary_amount),
            "next_payment_date": u.next_payment_date,
            "note":u.note,
        }
        for u in updates
    ]



# 🔹 3. GET — Foydalanuvchining o‘z update’larini ko‘rish
@router.get("/member/updates", summary="Foydalanuvchining o‘z update’larini olish")
async def get_member_updates(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    result = await session.execute(
        select(monthly_update).where(monthly_update.c.user_id == current_user.id)
    )
    updates = result.fetchall()
    return [
        {
            "id": u.id,
            "year": u.year,
            "month": u.month,
            "update_date": u.update_date,
            "update_percentage": float(u.update_percentage),
            "salary_amount": float(u.salary_amount),
            "next_payment_date": u.next_payment_date,
            "note":u.note,
        }
        for u in updates
    ]


# 🔹 4. PUT — To‘liq update’ni tahrirlash (faqat ruxsat bilan)
@router.put("/member/update/{update_id}", summary="Update’ni tahrirlash (to‘liq)")
async def edit_update(
    update_id: int,
    year: int,
    month: str,
    update_percentage: float,
    salary_amount: float,
    next_payment_date: date,
    note:str,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    update_data = {
        "year": year,
        "month": month,
        "update_percentage": update_percentage,
        "salary_amount": salary_amount,
        "next_payment_date": next_payment_date,
        "note": note,
    }

    result = await session.execute(
        update(monthly_update).where(monthly_update.c.id == update_id).values(**update_data)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Update topilmadi")

    return {"message": "Update muvaffaqiyatli tahrirlandi"}


# 🔹 5. PATCH — Qisman yangilash (faqat ruxsat bilan)
@router.patch("/member/update/{update_id}", summary="Update’ni qisman yangilash")
async def patch_update(
    update_id: int,
    update_percentage: float = None,
    salary_amount: float = None,
    next_payment_date: date = None,
    note: str = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    update_data = {}
    if update_percentage is not None:
        update_data["update_percentage"] = update_percentage
    if salary_amount is not None:
        update_data["salary_amount"] = salary_amount
    if next_payment_date is not None:
        update_data["next_payment_date"] = next_payment_date
    if note is not None:
        update_data["note"] = note

    if not update_data:
        raise HTTPException(status_code=400, detail="Yangilanadigan maydon topilmadi")

    result = await session.execute(
        update(monthly_update).where(monthly_update.c.id == update_id).values(**update_data)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Update topilmadi")

    return {"message": "Update ma'lumotlari yangilandi"}


# 🔹 6. DELETE — Update’ni o‘chirish (faqat ruxsat bilan)
@router.delete("/member/update/{update_id}", summary="Update’ni o‘chirish")
async def delete_update(
    update_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    result = await session.execute(
        delete(monthly_update).where(monthly_update.c.id == update_id)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Update topilmadi")

    return {"message": "Update muvaffaqiyatli o‘chirildi"}
