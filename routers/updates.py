
from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete, func, and_, or_
from models.user_models import (
    user_page_permission,
    monthly_update,
    user,
    monthly_penalty,
    monthly_bonus,
    UserRole,
)
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

MONTH_NUMBER_TO_UZ_NAME = {
    1: "Yanvar",
    2: "Fevral",
    3: "Mart",
    4: "Aprel",
    5: "May",
    6: "Iyun",
    7: "Iyul",
    8: "Avgust",
    9: "Sentabr",
    10: "Oktabr",
    11: "Noyabr",
    12: "Dekabr",
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


def parse_month_to_number(raw_month: Optional[str]) -> Optional[int]:
    if raw_month is None:
        return None
    normalized = str(raw_month).strip().lower()
    for month_num, aliases in MONTH_FILTER_ALIASES.items():
        if normalized in aliases:
            return month_num
    return None


def is_ceo_user(current_user) -> bool:
    role = getattr(current_user, "role", None)
    role_name = getattr(role, "name", None)
    role_value = getattr(role, "value", None)
    company_code = str(getattr(current_user, "company_code", "") or "").strip().lower()

    role_name_normalized = str(role_name or "").strip().lower()
    role_value_normalized = str(role_value or "").strip().lower()
    role_plain_normalized = str(role or "").strip().lower()

    return (
        role_name_normalized == "ceo"
        or role_value_normalized == "ceo"
        or role_plain_normalized == "ceo"
        or company_code == "ceo"
    )


def member_only_filter():
    return user.c.role == UserRole.member


def calculate_salary_estimate(
    base_salary: Decimal,
    total_penalty_points: Decimal,
    total_bonus_amount: Decimal = Decimal("0")
) -> dict:
    clamped_penalty = min(max(total_penalty_points, Decimal("0")), Decimal("100"))
    deduction_amount = (base_salary * clamped_penalty / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    salary_after_penalty = (base_salary - deduction_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    salary_after_penalty = max(salary_after_penalty, Decimal("0"))
    final_salary = (salary_after_penalty + total_bonus_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "base_salary": as_money(base_salary),
        "total_penalty_points": as_money(total_penalty_points),
        "penalty_percentage": as_money(clamped_penalty),
        "deduction_amount": as_money(deduction_amount),
        "salary_after_penalty": as_money(salary_after_penalty),
        "total_bonus_amount": as_money(total_bonus_amount),
        "final_salary": as_money(final_salary),
        "estimated_salary": as_money(final_salary)
    }


async def get_penalty_bonus_maps(
    session: AsyncSession,
    user_ids: List[int],
    year: int,
    month: int
):
    if not user_ids:
        return {}, {}

    penalties_result = await session.execute(
        select(
            monthly_penalty.c.user_id,
            func.coalesce(func.sum(monthly_penalty.c.penalty_points), 0).label("total_penalty_points"),
            func.count(monthly_penalty.c.id).label("penalties_count"),
        )
        .where(
            and_(
                monthly_penalty.c.user_id.in_(user_ids),
                monthly_penalty.c.year == year,
                monthly_penalty.c.month == month,
            )
        )
        .group_by(monthly_penalty.c.user_id)
    )
    penalty_map = {row.user_id: row for row in penalties_result.fetchall()}

    bonuses_result = await session.execute(
        select(
            monthly_bonus.c.user_id,
            func.coalesce(func.sum(monthly_bonus.c.bonus_amount), 0).label("total_bonus_amount"),
            func.count(monthly_bonus.c.id).label("bonuses_count"),
        )
        .where(
            and_(
                monthly_bonus.c.user_id.in_(user_ids),
                monthly_bonus.c.year == year,
                monthly_bonus.c.month == month,
            )
        )
        .group_by(monthly_bonus.c.user_id)
    )
    bonus_map = {row.user_id: row for row in bonuses_result.fetchall()}

    return penalty_map, bonus_map


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
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yoвЂq")

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
        "message": "Jarima ball muvaffaqiyatli qoвЂshildi",
        "penalty_id": penalty_id,
        "user_id": user_id,
        "year": year,
        "month": month,
        "penalty_points": penalty_points
    }


@router.put("/member/penalties/{penalty_id}", summary="Jarimani to'liq tahrirlash")
async def edit_member_penalty(
    penalty_id: int,
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

    result = await session.execute(
        update(monthly_penalty)
        .where(monthly_penalty.c.id == penalty_id)
        .values(
            year=year,
            month=month,
            penalty_points=Decimal(str(penalty_points)),
            reason=reason
        )
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Jarima topilmadi")

    return {"message": "Jarima muvaffaqiyatli yangilandi", "penalty_id": penalty_id}


@router.delete("/member/penalties/{penalty_id}", summary="Jarimani o'chirish")
async def delete_member_penalty(
    penalty_id: int,
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

    result = await session.execute(
        delete(monthly_penalty).where(monthly_penalty.c.id == penalty_id)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Jarima topilmadi")

    return {"message": "Jarima o‘chirildi", "penalty_id": penalty_id}


@router.post("/member/bonuses/add", summary="CEO tomonidan bonus qo'shish")
async def add_member_bonus(
    user_id: int,
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    bonus_amount: float = Query(..., gt=0),
    reason: Optional[str] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Faqat CEO bonus qo'sha oladi")

    user_result = await session.execute(select(user.c.id).where(user.c.id == user_id))
    if not user_result.fetchone():
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    result = await session.execute(
        insert(monthly_bonus)
        .values(
            user_id=user_id,
            year=year,
            month=month,
            bonus_amount=Decimal(str(bonus_amount)),
            reason=reason,
            created_by=current_user.id,
            created_at=datetime.now()
        )
        .returning(monthly_bonus.c.id)
    )
    bonus_id = result.scalar()
    await session.commit()

    return {
        "message": "Bonus muvaffaqiyatli qo'shildi",
        "bonus_id": bonus_id,
        "user_id": user_id,
        "year": year,
        "month": month,
        "bonus_amount": bonus_amount
    }


@router.put("/member/bonuses/{bonus_id}", summary="Bonusni tahrirlash")
async def edit_member_bonus(
    bonus_id: int,
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    bonus_amount: float = Query(..., gt=0),
    reason: Optional[str] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Faqat CEO bonusni tahrirlay oladi")

    result = await session.execute(
        update(monthly_bonus)
        .where(monthly_bonus.c.id == bonus_id)
        .values(
            year=year,
            month=month,
            bonus_amount=Decimal(str(bonus_amount)),
            reason=reason
        )
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bonus topilmadi")

    return {"message": "Bonus muvaffaqiyatli yangilandi", "bonus_id": bonus_id}


@router.delete("/member/bonuses/{bonus_id}", summary="Bonusni o'chirish")
async def delete_member_bonus(
    bonus_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Faqat CEO bonusni o'chira oladi")

    result = await session.execute(delete(monthly_bonus).where(monthly_bonus.c.id == bonus_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bonus topilmadi")

    return {"message": "Bonus o‘chirildi", "bonus_id": bonus_id}


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
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yoвЂq")

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

    bonuses_result = await session.execute(
        select(
            monthly_bonus.c.id,
            monthly_bonus.c.bonus_amount,
            monthly_bonus.c.reason,
            monthly_bonus.c.created_at
        )
        .where(
            and_(
                monthly_bonus.c.user_id == user_id,
                monthly_bonus.c.year == year,
                monthly_bonus.c.month == month
            )
        )
        .order_by(monthly_bonus.c.created_at.asc())
    )
    bonus_rows = bonuses_result.fetchall()

    total_bonus_amount = Decimal("0")
    for row in bonus_rows:
        total_bonus_amount += Decimal(str(row.bonus_amount or 0))

    base_salary = Decimal(str(user_data.default_salary or 0))
    estimate = calculate_salary_estimate(base_salary, total_penalty_points, total_bonus_amount)

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
        "bonuses_count": len(bonus_rows),
        "bonuses": [
            {
                "id": row.id,
                "bonus_amount": float(row.bonus_amount or 0),
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if row.created_at else None
            }
            for row in bonus_rows
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
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yoвЂq")

    selected_employee_ids = parse_employee_ids(employee_ids)

    users_query = select(
        user.c.id,
        user.c.name,
        user.c.surname,
        user.c.default_salary
    ).where(member_only_filter())
    if selected_employee_ids:
        users_query = users_query.where(user.c.id.in_(selected_employee_ids))
    users_query = users_query.order_by(user.c.name, user.c.surname)

    users_result = await session.execute(users_query)
    users_rows = users_result.fetchall()

    penalty_map, bonus_map = await get_penalty_bonus_maps(
        session=session,
        user_ids=[row.id for row in users_rows],
        year=year,
        month=month
    )

    employees = []
    total_base_salary = Decimal("0")
    total_final_salary = Decimal("0")
    total_deduction = Decimal("0")
    total_bonus = Decimal("0")

    for row in users_rows:
        base_salary = Decimal(str(row.default_salary or 0))
        penalty_row = penalty_map.get(row.id)
        bonus_row = bonus_map.get(row.id)
        total_penalty_points = Decimal(str(penalty_row.total_penalty_points if penalty_row else 0))
        penalties_count = int(penalty_row.penalties_count) if penalty_row else 0
        total_bonus_amount = Decimal(str(bonus_row.total_bonus_amount if bonus_row else 0))
        bonuses_count = int(bonus_row.bonuses_count) if bonus_row else 0

        estimate = calculate_salary_estimate(base_salary, total_penalty_points, total_bonus_amount)
        total_base_salary += Decimal(str(estimate["base_salary"]))
        total_final_salary += Decimal(str(estimate["final_salary"]))
        total_deduction += Decimal(str(estimate["deduction_amount"]))
        total_bonus += Decimal(str(estimate["total_bonus_amount"]))

        employees.append({
            "user_id": row.id,
            "full_name": f"{row.name} {row.surname}",
            "penalties_count": penalties_count,
            "bonuses_count": bonuses_count,
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
            "total_bonus_amount": as_money(total_bonus),
            "total_final_salary": as_money(total_final_salary),
            "total_estimated_salary": as_money(total_final_salary)
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
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yoвЂq")

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
        .where(member_only_filter())
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

        penalty_map, bonus_map = await get_penalty_bonus_maps(
            session=session,
            user_ids=employee_id_list,
            year=year,
            month=month
        )

        total_base_salary = Decimal("0")
        total_deduction_amount = Decimal("0")
        total_bonus_amount = Decimal("0")
        total_final_salary = Decimal("0")

        for employee_id in employee_id_list:
            base_salary = salary_base_map.get(employee_id, Decimal("0"))
            penalty_row = penalty_map.get(employee_id)
            bonus_row = bonus_map.get(employee_id)
            total_penalty_points = Decimal(str(penalty_row.total_penalty_points if penalty_row else 0))
            penalties_count = int(penalty_row.penalties_count) if penalty_row else 0
            total_bonus = Decimal(str(bonus_row.total_bonus_amount if bonus_row else 0))
            bonuses_count = int(bonus_row.bonuses_count) if bonus_row else 0

            estimate = calculate_salary_estimate(base_salary, total_penalty_points, total_bonus)
            estimate["penalties_count"] = penalties_count
            estimate["bonuses_count"] = bonuses_count
            salary_estimate_map[employee_id] = estimate

            total_base_salary += Decimal(str(estimate["base_salary"]))
            total_deduction_amount += Decimal(str(estimate["deduction_amount"]))
            total_bonus_amount += Decimal(str(estimate["total_bonus_amount"]))
            total_final_salary += Decimal(str(estimate["final_salary"]))

        salary_summary = {
            "total_base_salary": as_money(total_base_salary),
            "total_deduction_amount": as_money(total_deduction_amount),
            "total_bonus_amount": as_money(total_bonus_amount),
            "total_final_salary": as_money(total_final_salary),
            "total_estimated_salary": as_money(total_final_salary)
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


# рџ”№ 1. CREATE вЂ” Yangi oy maвЂ™lumotini kiritish (faqat update_list permission bilan)
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
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yoвЂq")

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
    return {"message": f"{month}/{year} uchun update muvaffaqiyatli qoвЂshildi"}


# рџ”№ 2. GET вЂ” Hamma foydalanuvchilar uchun barcha updateвЂ™lar (faqat update_list sahifasiga ruxsati borlar uchun)
@router.get("/member/updates/all", summary="CEO uchun employee update/jarima/oylik statistikasi (oylar kesimida)")
async def get_all_updates(
    year: Optional[int] = Query(default=None, ge=2020, le=2035),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    employee_ids: Optional[str] = Query(default=None, description="Comma-separated user IDs. Masalan: 1,2,3"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Faqat CEO bu ma'lumotni ko'ra oladi")

    selected_employee_ids = parse_employee_ids(employee_ids)

    employees_query = (
        select(
            user.c.id,
            user.c.name,
            user.c.surname,
            user.c.default_salary
        )
        .where(
            and_(
                user.c.is_active == True,
                member_only_filter(),
            )
        )
        .order_by(user.c.name, user.c.surname)
    )
    if selected_employee_ids:
        employees_query = employees_query.where(user.c.id.in_(selected_employee_ids))

    employees_result = await session.execute(employees_query)
    employee_rows = employees_result.fetchall()

    if not employee_rows:
        return {
            "filters": {
                "year": year,
                "month": month,
                "employee_ids": selected_employee_ids
            },
            "summary": {
                "employees_count": 0,
                "periods_count": 0,
                "total_reports": 0,
                "average_update_percentage": 0.0
            },
            "employees": []
        }

    employee_id_list = [row.id for row in employee_rows]
    employee_id_set = set(employee_id_list)

    updates_query = (
        select(
            monthly_update.c.user_id,
            monthly_update.c.year,
            monthly_update.c.month,
            monthly_update.c.update_percentage,
            monthly_update.c.salary_amount,
            monthly_update.c.update_date
        )
        .where(monthly_update.c.user_id.in_(employee_id_list))
    )

    if year is not None:
        updates_query = updates_query.where(monthly_update.c.year == year)
    if month is not None:
        month_values = {item.lower() for item in MONTH_FILTER_ALIASES.get(month, set())}
        updates_query = updates_query.where(func.lower(func.trim(monthly_update.c.month)).in_(month_values))

    updates_result = await session.execute(updates_query)
    update_rows = updates_result.fetchall()

    penalties_query = (
        select(
            monthly_penalty.c.user_id,
            monthly_penalty.c.year,
            monthly_penalty.c.month,
            func.coalesce(func.sum(monthly_penalty.c.penalty_points), 0).label("total_penalty_points")
        )
        .where(monthly_penalty.c.user_id.in_(employee_id_list))
        .group_by(monthly_penalty.c.user_id, monthly_penalty.c.year, monthly_penalty.c.month)
    )
    if year is not None:
        penalties_query = penalties_query.where(monthly_penalty.c.year == year)
    if month is not None:
        penalties_query = penalties_query.where(monthly_penalty.c.month == month)

    penalties_result = await session.execute(penalties_query)
    penalty_rows = penalties_result.fetchall()

    penalties_map = {}
    employee_penalty_periods = {employee_id: set() for employee_id in employee_id_list}
    for row in penalty_rows:
        key = (row.user_id, int(row.year), int(row.month))
        penalties_map[key] = Decimal(str(row.total_penalty_points or 0))
        employee_penalty_periods[row.user_id].add((int(row.year), int(row.month)))

    bonuses_query = (
        select(
            monthly_bonus.c.user_id,
            monthly_bonus.c.year,
            monthly_bonus.c.month,
            func.coalesce(func.sum(monthly_bonus.c.bonus_amount), 0).label("total_bonus_amount")
        )
        .where(monthly_bonus.c.user_id.in_(employee_id_list))
        .group_by(monthly_bonus.c.user_id, monthly_bonus.c.year, monthly_bonus.c.month)
    )
    if year is not None:
        bonuses_query = bonuses_query.where(monthly_bonus.c.year == year)
    if month is not None:
        bonuses_query = bonuses_query.where(monthly_bonus.c.month == month)

    bonuses_result = await session.execute(bonuses_query)
    bonus_rows = bonuses_result.fetchall()

    bonuses_map = {}
    employee_bonus_periods = {employee_id: set() for employee_id in employee_id_list}
    for row in bonus_rows:
        key = (row.user_id, int(row.year), int(row.month))
        bonuses_map[key] = Decimal(str(row.total_bonus_amount or 0))
        employee_bonus_periods[row.user_id].add((int(row.year), int(row.month)))

    monthly_updates_map = {employee_id: {} for employee_id in employee_id_list}
    for row in update_rows:
        if row.user_id not in employee_id_set:
            continue

        month_number = parse_month_to_number(row.month)
        if month is not None and month_number != month:
            continue

        period_key = (int(row.year), month_number)
        if period_key not in monthly_updates_map[row.user_id]:
            monthly_updates_map[row.user_id][period_key] = {
                "year": int(row.year),
                "month": month_number,
                "month_name": MONTH_NUMBER_TO_UZ_NAME.get(month_number, row.month),
                "reports_count": 0,
                "update_percentage_sum": Decimal("0"),
                "total_salary_amount": Decimal("0"),
                "latest_report_date": None
            }

        period_item = monthly_updates_map[row.user_id][period_key]
        period_item["reports_count"] += 1
        period_item["update_percentage_sum"] += Decimal(str(row.update_percentage or 0))
        period_item["total_salary_amount"] += Decimal(str(row.salary_amount or 0))

        if row.update_date and (
            period_item["latest_report_date"] is None
            or row.update_date > period_item["latest_report_date"]
        ):
            period_item["latest_report_date"] = row.update_date

    employees_response = []
    total_periods = 0
    total_reports = 0
    total_update_percentage_sum = Decimal("0")

    for employee in employee_rows:
        employee_periods = monthly_updates_map.get(employee.id, {})
        penalty_periods = employee_penalty_periods.get(employee.id, set())
        bonus_periods = employee_bonus_periods.get(employee.id, set())

        merged_keys = set(employee_periods.keys()) | penalty_periods | bonus_periods
        sorted_keys = sorted(
            merged_keys,
            key=lambda item: (
                item[0],
                item[1] if item[1] is not None else 13
            )
        )

        periods_response = []
        employee_reports_count = 0
        employee_update_percentage_sum = Decimal("0")

        base_salary = Decimal(str(employee.default_salary or 0))

        for period_key in sorted_keys:
            period_update = employee_periods.get(period_key)
            period_year, period_month = period_key
            total_penalty_points = penalties_map.get((employee.id, period_year, period_month or 0), Decimal("0"))
            total_bonus_amount = bonuses_map.get((employee.id, period_year, period_month or 0), Decimal("0"))
            salary_estimate = calculate_salary_estimate(base_salary, total_penalty_points, total_bonus_amount)

            if period_update:
                reports_count = period_update["reports_count"]
                average_update_percentage = (
                    period_update["update_percentage_sum"] / reports_count
                    if reports_count > 0 else Decimal("0")
                )
                total_salary_amount = period_update["total_salary_amount"]
                latest_report_date = period_update["latest_report_date"]
            else:
                reports_count = 0
                average_update_percentage = Decimal("0")
                total_salary_amount = Decimal("0")
                latest_report_date = None

            employee_reports_count += reports_count
            employee_update_percentage_sum += average_update_percentage * reports_count

            periods_response.append({
                "year": period_year,
                "month": period_month,
                "month_name": MONTH_NUMBER_TO_UZ_NAME.get(period_month, str(period_month) if period_month else None),
                "reports_count": reports_count,
                "average_update_percentage": round(float(average_update_percentage), 2),
                "total_penalty_points": as_money(total_penalty_points),
                "total_bonus_amount": as_money(total_bonus_amount),
                "total_salary_amount": as_money(total_salary_amount),
                "salary_estimate": salary_estimate,
                "latest_report_date": str(latest_report_date) if latest_report_date else None
            })

        employee_average_update = (
            employee_update_percentage_sum / employee_reports_count
            if employee_reports_count > 0 else Decimal("0")
        )

        total_periods += len(periods_response)
        total_reports += employee_reports_count
        total_update_percentage_sum += employee_update_percentage_sum

        employees_response.append({
            "user_id": employee.id,
            "full_name": f"{employee.name} {employee.surname}",
            "default_salary": as_money(base_salary),
            "summary": {
                "periods_count": len(periods_response),
                "total_reports": employee_reports_count,
                "average_update_percentage": round(float(employee_average_update), 2)
            },
            "periods": periods_response
        })

    average_update_percentage = (
        total_update_percentage_sum / total_reports
        if total_reports > 0 else Decimal("0")
    )

    return {
        "filters": {
            "year": year,
            "month": month,
            "employee_ids": selected_employee_ids
        },
        "summary": {
            "employees_count": len(employees_response),
            "periods_count": total_periods,
            "total_reports": total_reports,
            "average_update_percentage": round(float(average_update_percentage), 2)
        },
        "employees": employees_response
    }


@router.get("/member/updates", summary="Foydalanuvchining oвЂz updateвЂ™larini olish")
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


# рџ”№ 4. PUT вЂ” ToвЂliq updateвЂ™ni tahrirlash (faqat ruxsat bilan)
@router.put("/member/update/{update_id}", summary="UpdateвЂ™ni tahrirlash (toвЂliq)")
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
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yoвЂq")

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


# рџ”№ 5. PATCH вЂ” Qisman yangilash (faqat ruxsat bilan)
@router.patch("/member/update/{update_id}", summary="UpdateвЂ™ni qisman yangilash")
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
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yoвЂq")

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


# рџ”№ 6. DELETE вЂ” UpdateвЂ™ni oвЂchirish (faqat ruxsat bilan)
@router.delete("/member/update/{update_id}", summary="UpdateвЂ™ni oвЂchirish")
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
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yoвЂq")

    result = await session.execute(
        delete(monthly_update).where(monthly_update.c.id == update_id)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Update topilmadi")

    return {"message": "Update muvaffaqiyatli oвЂchirildi"}

