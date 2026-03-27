import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_active_user
from database import get_async_session
from models.admin_models import daily_update_log
from models.projects_models import project
from models.user_models import (
    CompensationBonusType,
    MistakeCategory,
    MistakeSeverity,
    UserRole,
    compensation_bonus,
    compensation_mistake,
    monthly_update,
    user,
    user_page_permission,
)
from schemes.schemes_compensation import (
    CompensationMistakeCreateRequest,
    CompensationMistakeUpdateRequest,
    DeliveryBonusCreateRequest,
    DeliveryBonusUpdateRequest,
)
from utils.admin_stats import _is_excluded_from_admin_stats
from utils.compensation_policy import (
    CATEGORY_LABELS,
    DEVELOPER_SHARE_PERCENT,
    DELIVERY_BONUS_RATE_BY_TYPE,
    MAX_MONTHLY_DEDUCTION_PERCENT,
    PRODUCTIVITY_BONUS_FULL_UPDATES,
    QUALITY_BONUS_NO_CLIENT_MISTAKES,
    QUALITY_BONUS_NO_MAJOR_CRITICAL,
    REVIEWER_SHARE_PERCENT,
    as_money,
    bonus_amount_from_percent,
    deduction_rate_for_severity,
    deduction_amount_for_severity,
    delivery_bonus_rate,
    max_monthly_deduction_amount,
    normalize_base_salary,
    proportional_cap,
    quantize_money,
)
from utils.workday_overrides import fetch_override_pack, list_expected_update_days

router = APIRouter(prefix="/members", tags=["Employees Api"])

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

SEVERITY_DECISION_TREE = [
    {"step": 1, "question": "Did the mistake reach the client?", "if_yes": "Continue", "if_no": "No deduction"},
    {"step": 2, "question": "Did the system completely stop working?", "if_yes": "Critical"},
    {"step": 3, "question": "Did the mistake break an important feature?", "if_yes": "Major"},
    {"step": 4, "question": "Is the feature working but incorrectly?", "if_yes": "Moderate"},
    {"step": 5, "question": "Is it only a small inconvenience?", "if_yes": "Minor"},
]


def parse_employee_ids(employee_ids: Optional[str]) -> List[int]:
    if not employee_ids:
        return []
    parsed_ids = []
    for raw_id in employee_ids.split(","):
        normalized = raw_id.strip()
        if not normalized:
            continue
        if not normalized.isdigit():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="employee_ids noto'g'ri formatda. Misol: 1,2,3",
            )
        parsed_ids.append(int(normalized))
    return list(dict.fromkeys(parsed_ids))


def parse_month_to_number(raw_month: Optional[str]) -> Optional[int]:
    if raw_month is None:
        return None
    normalized = str(raw_month).strip().lower()
    for month_num, aliases in MONTH_FILTER_ALIASES.items():
        if normalized in aliases:
            return month_num
    return None


def member_only_filter():
    return user.c.role == UserRole.member


def is_visible_update_member(name: Optional[str], surname: Optional[str]) -> bool:
    return not _is_excluded_from_admin_stats(name, surname)


def is_ceo_user(current_user) -> bool:
    role = getattr(current_user, "role", None)
    role_name = str(getattr(role, "name", "") or "").strip().lower()
    role_value = str(getattr(role, "value", "") or "").strip().lower()
    role_plain = str(role or "").strip().lower()
    company_code = str(getattr(current_user, "company_code", "") or "").strip().lower()
    return role_name == "ceo" or role_value == "ceo" or role_plain == "ceo" or company_code == "ceo"


async def has_update_list_permission(session: AsyncSession, current_user) -> bool:
    result = await session.execute(
        select(user_page_permission.c.id).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list",
        )
    )
    return result.fetchone() is not None


async def ensure_compensation_access(session: AsyncSession, current_user) -> None:
    if is_ceo_user(current_user):
        return
    if await has_update_list_permission(session, current_user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Compensation bo'limiga kirish huquqingiz yo'q",
    )


async def ensure_user_exists(
    session: AsyncSession,
    user_id: Optional[int],
    detail_message: str = "Foydalanuvchi topilmadi",
):
    if user_id is None:
        return None
    result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.role).where(user.c.id == user_id)
    )
    user_row = result.fetchone()
    if not user_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail_message)
    return user_row


async def ensure_member_exists(
    session: AsyncSession,
    user_id: int,
    detail_message: str = "Employee topilmadi",
):
    result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.default_salary)
        .where(and_(user.c.id == user_id, member_only_filter()))
    )
    user_row = result.fetchone()
    if not user_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail_message)
    return user_row


async def ensure_project_exists(
    session: AsyncSession,
    project_id: Optional[int],
) -> None:
    if project_id is not None:
        project_result = await session.execute(select(project.c.id).where(project.c.id == project_id))
        if project_result.scalar() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project topilmadi")


def get_period_bounds(year: int, month: int) -> tuple[date, date]:
    if month < 1 or month > 12:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="month 1 dan 12 gacha bo'lishi kerak")
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return first_day, last_day


def get_reporting_end_date(first_day: date, last_day: date) -> date:
    return min(last_day, date.today())


def exclude_pending_today_from_expected_days(expected_days: List[date], submitted_dates: set[date]) -> List[date]:
    today = date.today()
    if today in submitted_dates or today not in expected_days:
        return expected_days
    return [day for day in expected_days if day != today]


async def calculate_monthly_update_coverage(
    session: AsyncSession,
    user_id: int,
    year: int,
    month: int,
) -> dict:
    first_day, last_day = get_period_bounds(year, month)
    reporting_end = get_reporting_end_date(first_day, last_day)
    updates_result = await session.execute(
        select(daily_update_log.c.update_date)
        .distinct()
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= first_day,
                daily_update_log.c.update_date <= reporting_end,
                daily_update_log.c.is_valid == True,  # noqa: E712
            )
        )
    )
    submitted_dates = {row.update_date for row in updates_result.fetchall()}
    override_pack = await fetch_override_pack(session, first_day, last_day, user_ids=[user_id])
    expected_workdays = list_expected_update_days(override_pack, user_id, first_day, reporting_end)
    expected_workdays = exclude_pending_today_from_expected_days(expected_workdays, submitted_dates)
    expected_dates = set(expected_workdays)
    update_days = len(submitted_dates & expected_dates)
    working_days = len(expected_workdays)
    percentage = round((update_days / working_days) * 100, 2) if working_days > 0 else 0.0
    return {
        "working_days": working_days,
        "update_days": update_days,
        "percentage": percentage,
        "qualifies_productivity_bonus": working_days > 0 and percentage >= 100,
    }


def build_policy_payload(base_salary: Decimal | float | int | None) -> dict:
    salary_base = normalize_base_salary(base_salary)
    return {
        "salary_base": as_money(salary_base),
        "monthly_deduction_cap_percent": as_money(MAX_MONTHLY_DEDUCTION_PERCENT),
        "monthly_deduction_cap_amount": as_money(max_monthly_deduction_amount(salary_base)),
        "responsibility_split": {
            "developer_percent": as_money(DEVELOPER_SHARE_PERCENT),
            "reviewer_percent": as_money(REVIEWER_SHARE_PERCENT),
        },
        "deduction_rates": [
            {
                "severity": severity.value,
                "percent": as_money(percent),
                "amount": as_money(deduction_amount_for_severity(salary_base, severity)),
            }
            for severity, percent in [
                (MistakeSeverity.minor, Decimal("2")),
                (MistakeSeverity.moderate, Decimal("5")),
                (MistakeSeverity.major, Decimal("10")),
                (MistakeSeverity.critical, Decimal("20")),
            ]
        ],
        "bonus_rates": {
            "productivity_full_updates_percent": as_money(PRODUCTIVITY_BONUS_FULL_UPDATES),
            "quality_no_client_mistakes_percent": as_money(QUALITY_BONUS_NO_CLIENT_MISTAKES),
            "quality_no_major_critical_percent": as_money(QUALITY_BONUS_NO_MAJOR_CRITICAL),
            "early_delivery_percent": as_money(DELIVERY_BONUS_RATE_BY_TYPE[CompensationBonusType.early_delivery]),
            "major_early_delivery_percent": as_money(
                DELIVERY_BONUS_RATE_BY_TYPE[CompensationBonusType.major_early_delivery]
            ),
        },
        "mistake_categories": [
            {"key": category.name, "value": category.value, "label": CATEGORY_LABELS.get(category, category.value)}
            for category in MistakeCategory
        ],
        "severities": [severity.value for severity in MistakeSeverity],
        "decision_tree": SEVERITY_DECISION_TREE,
    }


def build_incident_role_preview(row) -> dict:
    employee_salary = normalize_base_salary(getattr(row, "employee_default_salary", 0))
    reviewer_salary = normalize_base_salary(getattr(row, "reviewer_default_salary", 0))
    developer_base_amount = deduction_amount_for_severity(employee_salary, row.severity) if row.reached_client else Decimal("0")
    reviewer_base_amount = deduction_amount_for_severity(reviewer_salary, row.severity) if row.reached_client else Decimal("0")
    if not row.reached_client:
        developer_percent = Decimal("0")
        reviewer_percent = Decimal("0")
    elif row.unclear_task:
        developer_percent = Decimal("0")
        reviewer_percent = Decimal("100")
    elif row.reviewer_id:
        developer_percent = DEVELOPER_SHARE_PERCENT
        reviewer_percent = REVIEWER_SHARE_PERCENT
    else:
        developer_percent = Decimal("100")
        reviewer_percent = Decimal("0")

    developer_amount = quantize_money(developer_base_amount * developer_percent / Decimal("100"))
    reviewer_amount = quantize_money(reviewer_base_amount * reviewer_percent / Decimal("100"))
    return {
        "severity_base_percent": as_money(deduction_rate_for_severity(row.severity)),
        "developer_salary_base": as_money(employee_salary),
        "reviewer_salary_base": as_money(reviewer_salary),
        "developer_share_percent": as_money(developer_percent),
        "reviewer_share_percent": as_money(reviewer_percent),
        "developer_deduction_amount": as_money(developer_amount),
        "reviewer_deduction_amount": as_money(reviewer_amount),
        "total_deduction_amount": as_money(developer_amount + reviewer_amount),
    }


def build_user_deduction_breakdown(user_id: int, user_base_salary: Decimal | float | int | None, incidents: List) -> dict:
    salary_base = normalize_base_salary(user_base_salary)
    detail_items = []
    positive_raw_amounts: List[Decimal] = []

    for row in incidents:
        total_incident_amount = deduction_amount_for_severity(salary_base, row.severity) if row.reached_client else Decimal("0")
        if row.unclear_task:
            if row.employee_id == user_id:
                role = "developer"
                share_percent = Decimal("0")
            elif row.reviewer_id == user_id:
                role = "reviewer"
                share_percent = Decimal("100")
            else:
                continue
        elif row.employee_id == user_id:
            role = "developer"
            share_percent = DEVELOPER_SHARE_PERCENT if row.reviewer_id else Decimal("100")
        elif row.reviewer_id == user_id:
            role = "reviewer"
            share_percent = REVIEWER_SHARE_PERCENT
        else:
            continue

        raw_deduction_amount = (
            quantize_money(total_incident_amount * share_percent / Decimal("100"))
            if row.reached_client
            else Decimal("0")
        )
        detail_items.append(
            {
                "id": row.id,
                "role": role,
                "category": row.category.value,
                "severity": row.severity.value,
                "title": row.title,
                "description": row.description,
                "incident_date": row.incident_date.isoformat(),
                "employee_id": row.employee_id,
                "employee_full_name": f"{row.employee_name} {row.employee_surname}".strip(),
                "reviewer_id": row.reviewer_id,
                "reviewer_full_name": (
                    f"{row.reviewer_name} {row.reviewer_surname}".strip()
                    if row.reviewer_name or row.reviewer_surname
                    else None
                ),
                "project_id": row.project_id,
                "reached_client": bool(row.reached_client),
                "unclear_task": bool(row.unclear_task),
                "share_percent": as_money(share_percent),
                "total_incident_deduction_amount": as_money(total_incident_amount),
                "raw_deduction_amount_decimal": raw_deduction_amount,
                "raw_deduction_amount": as_money(raw_deduction_amount),
                "applied_deduction_amount": 0.0,
            }
        )
        if raw_deduction_amount > 0:
            positive_raw_amounts.append(raw_deduction_amount)

    cap_amount = max_monthly_deduction_amount(salary_base)
    capped_positive_amounts = proportional_cap(positive_raw_amounts, cap_amount)
    raw_total = Decimal("0")
    applied_total = Decimal("0")
    positive_index = 0
    for item in detail_items:
        raw_decimal = item.pop("raw_deduction_amount_decimal")
        raw_total += raw_decimal
        if raw_decimal > 0:
            applied_decimal = capped_positive_amounts[positive_index]
            positive_index += 1
        else:
            applied_decimal = Decimal("0")
        applied_total += applied_decimal
        item["applied_deduction_amount"] = as_money(applied_decimal)

    return {
        "items": detail_items,
        "raw_total": raw_total,
        "applied_total": applied_total,
        "cap_amount": cap_amount,
        "cap_applied": raw_total > cap_amount,
    }


async def fetch_user_period_incidents(
    session: AsyncSession,
    user_id: int,
    first_day: date,
    last_day: date,
) -> List:
    employee_user = user.alias("employee_user")
    reviewer_user = user.alias("reviewer_user")
    result = await session.execute(
        select(
            compensation_mistake.c.id,
            compensation_mistake.c.employee_id,
            compensation_mistake.c.reviewer_id,
            compensation_mistake.c.project_id,
            compensation_mistake.c.category,
            compensation_mistake.c.severity,
            compensation_mistake.c.title,
            compensation_mistake.c.description,
            compensation_mistake.c.incident_date,
            compensation_mistake.c.reached_client,
            compensation_mistake.c.unclear_task,
            employee_user.c.name.label("employee_name"),
            employee_user.c.surname.label("employee_surname"),
            employee_user.c.default_salary.label("employee_default_salary"),
            reviewer_user.c.name.label("reviewer_name"),
            reviewer_user.c.surname.label("reviewer_surname"),
            reviewer_user.c.default_salary.label("reviewer_default_salary"),
        )
        .select_from(
            compensation_mistake
            .join(employee_user, compensation_mistake.c.employee_id == employee_user.c.id)
            .outerjoin(reviewer_user, compensation_mistake.c.reviewer_id == reviewer_user.c.id)
        )
        .where(
            and_(
                compensation_mistake.c.incident_date >= first_day,
                compensation_mistake.c.incident_date <= last_day,
                or_(compensation_mistake.c.employee_id == user_id, compensation_mistake.c.reviewer_id == user_id),
            )
        )
        .order_by(compensation_mistake.c.incident_date.asc(), compensation_mistake.c.id.asc())
    )
    return result.fetchall()


async def fetch_employee_period_delivery_bonuses(
    session: AsyncSession,
    user_id: int,
    first_day: date,
    last_day: date,
) -> List:
    creator_user = user.alias("creator_user")
    result = await session.execute(
        select(
            compensation_bonus.c.id,
            compensation_bonus.c.employee_id,
            compensation_bonus.c.project_id,
            compensation_bonus.c.bonus_type,
            compensation_bonus.c.title,
            compensation_bonus.c.description,
            compensation_bonus.c.award_date,
            compensation_bonus.c.created_by,
            compensation_bonus.c.created_at,
            user.c.default_salary.label("employee_default_salary"),
            creator_user.c.name.label("creator_name"),
            creator_user.c.surname.label("creator_surname"),
        )
        .select_from(
            compensation_bonus
            .join(user, compensation_bonus.c.employee_id == user.c.id)
            .outerjoin(creator_user, compensation_bonus.c.created_by == creator_user.c.id)
        )
        .where(
            and_(
                compensation_bonus.c.employee_id == user_id,
                compensation_bonus.c.award_date >= first_day,
                compensation_bonus.c.award_date <= last_day,
            )
        )
        .order_by(compensation_bonus.c.award_date.asc(), compensation_bonus.c.id.asc())
    )
    return result.fetchall()


async def build_member_compensation_payload(
    session: AsyncSession,
    user_row,
    year: int,
    month: int,
    include_details: bool = True,
) -> dict:
    salary_base = normalize_base_salary(getattr(user_row, "default_salary", 0))
    first_day, last_day = get_period_bounds(year, month)
    incidents = await fetch_user_period_incidents(session, user_row.id, first_day, last_day)
    delivery_bonus_rows = await fetch_employee_period_delivery_bonuses(session, user_row.id, first_day, last_day)
    deduction_breakdown = build_user_deduction_breakdown(user_row.id, salary_base, incidents)
    update_coverage = await calculate_monthly_update_coverage(session, user_row.id, year, month)

    employee_client_mistakes = [row for row in incidents if row.employee_id == user_row.id and row.reached_client]
    if not employee_client_mistakes:
        quality_percent = QUALITY_BONUS_NO_CLIENT_MISTAKES
        quality_label = "No client-reported mistakes during the month"
    elif all(row.severity not in {MistakeSeverity.major, MistakeSeverity.critical} for row in employee_client_mistakes):
        quality_percent = QUALITY_BONUS_NO_MAJOR_CRITICAL
        quality_label = "No Major or Critical client-reported mistakes"
    else:
        quality_percent = Decimal("0")
        quality_label = "Quality bonus not applicable"

    productivity_percent = (
        PRODUCTIVITY_BONUS_FULL_UPDATES if update_coverage["qualifies_productivity_bonus"] else Decimal("0")
    )
    delivery_percent = Decimal("0")
    selected_delivery_bonus = None
    serialized_delivery_bonuses = []
    for row in delivery_bonus_rows:
        bonus_percent = delivery_bonus_rate(row.bonus_type)
        serialized_item = {
            "id": row.id,
            "bonus_type": row.bonus_type.value,
            "title": row.title,
            "description": row.description,
            "award_date": row.award_date.isoformat(),
            "project_id": row.project_id,
            "created_by": row.created_by,
            "created_by_full_name": (
                f"{row.creator_name} {row.creator_surname}".strip()
                if row.creator_name or row.creator_surname
                else None
            ),
            "bonus_percent": as_money(bonus_percent),
            "bonus_amount": as_money(bonus_amount_from_percent(salary_base, bonus_percent)),
        }
        serialized_delivery_bonuses.append(serialized_item)
        if bonus_percent >= delivery_percent:
            delivery_percent = bonus_percent
            selected_delivery_bonus = serialized_item

    total_bonus_percent = productivity_percent + quality_percent + delivery_percent
    total_bonus_amount = bonus_amount_from_percent(salary_base, total_bonus_percent)
    final_salary = quantize_money(salary_base - deduction_breakdown["applied_total"] + total_bonus_amount)

    payload = {
        "user": {
            "id": user_row.id,
            "full_name": f"{user_row.name} {user_row.surname}",
            "contract_salary": as_money(salary_base),
        },
        "period": {"year": year, "month": month, "month_name": MONTH_NUMBER_TO_UZ_NAME.get(month, str(month))},
        "policy": build_policy_payload(salary_base),
        "update_productivity": update_coverage,
        "mistakes_count": len(incidents),
        "delivery_bonus_count": len(delivery_bonus_rows),
        "bonuses_summary": {
            "automatic_components": [
                {
                    "type": "productivity",
                    "label": "100% updates completed",
                    "applied": bool(productivity_percent > 0),
                    "bonus_percent": as_money(productivity_percent),
                    "bonus_amount": as_money(bonus_amount_from_percent(salary_base, productivity_percent)),
                },
                {
                    "type": "quality",
                    "label": quality_label,
                    "applied": bool(quality_percent > 0),
                    "bonus_percent": as_money(quality_percent),
                    "bonus_amount": as_money(bonus_amount_from_percent(salary_base, quality_percent)),
                },
                {
                    "type": "delivery",
                    "label": selected_delivery_bonus["title"] if selected_delivery_bonus else "No delivery bonus selected",
                    "applied": bool(delivery_percent > 0),
                    "bonus_percent": as_money(delivery_percent),
                    "bonus_amount": as_money(bonus_amount_from_percent(salary_base, delivery_percent)),
                },
            ],
            "selected_delivery_bonus": selected_delivery_bonus,
            "total_bonus_percent": as_money(total_bonus_percent),
            "total_bonus_amount": as_money(total_bonus_amount),
        },
        "deductions_summary": {
            "raw_deduction_amount": as_money(deduction_breakdown["raw_total"]),
            "applied_deduction_amount": as_money(deduction_breakdown["applied_total"]),
            "cap_amount": as_money(deduction_breakdown["cap_amount"]),
            "cap_applied": deduction_breakdown["cap_applied"],
        },
        "salary_estimate": {
            "base_salary": as_money(salary_base),
            "raw_deduction_amount": as_money(deduction_breakdown["raw_total"]),
            "applied_deduction_amount": as_money(deduction_breakdown["applied_total"]),
            "total_bonus_percent": as_money(total_bonus_percent),
            "total_bonus_amount": as_money(total_bonus_amount),
            "final_salary": as_money(final_salary),
            "estimated_salary": as_money(final_salary),
        },
    }
    if include_details:
        payload["mistakes"] = deduction_breakdown["items"]
        payload["delivery_bonuses"] = serialized_delivery_bonuses
    return payload


@router.get("/compensation/policy", summary="Compensation policy configuration")
async def get_compensation_policy(
    employee_ids: Optional[str] = Query(default=None, description="Comma-separated employee IDs. Masalan: 1,2,3"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    selected_employee_ids = parse_employee_ids(employee_ids)
    users_query = (
        select(user.c.id, user.c.name, user.c.surname, user.c.default_salary)
        .where(and_(user.c.is_active == True, member_only_filter()))  # noqa: E712
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )
    if selected_employee_ids:
        users_query = users_query.where(user.c.id.in_(selected_employee_ids))

    rows = [
        row for row in (await session.execute(users_query)).fetchall() if is_visible_update_member(row.name, row.surname)
    ]
    return {
        "items": [
            {
                "employee": {"id": row.id, "full_name": f"{row.name} {row.surname}"},
                "policy": build_policy_payload(row.default_salary),
            }
            for row in rows
        ],
        "total_count": len(rows),
        "filters": {"employee_ids": selected_employee_ids},
    }


@router.get("/member/mistakes", summary="Mistake incidents list")
async def list_compensation_mistakes(
    year: Optional[int] = Query(default=None, ge=2020, le=2035),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    employee_id: Optional[int] = None,
    reviewer_id: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    employee_user = user.alias("employee_user")
    reviewer_user = user.alias("reviewer_user")
    conditions = []
    if employee_id is not None:
        conditions.append(compensation_mistake.c.employee_id == employee_id)
    if reviewer_id is not None:
        conditions.append(compensation_mistake.c.reviewer_id == reviewer_id)
    if year is not None:
        conditions.append(func.extract("year", compensation_mistake.c.incident_date) == year)
    if month is not None:
        conditions.append(func.extract("month", compensation_mistake.c.incident_date) == month)

    query = (
        select(
            compensation_mistake.c.id,
            compensation_mistake.c.employee_id,
            compensation_mistake.c.reviewer_id,
            compensation_mistake.c.project_id,
            compensation_mistake.c.category,
            compensation_mistake.c.severity,
            compensation_mistake.c.title,
            compensation_mistake.c.description,
            compensation_mistake.c.incident_date,
            compensation_mistake.c.reached_client,
            compensation_mistake.c.unclear_task,
            compensation_mistake.c.created_by,
            compensation_mistake.c.created_at,
            employee_user.c.name.label("employee_name"),
            employee_user.c.surname.label("employee_surname"),
            employee_user.c.default_salary.label("employee_default_salary"),
            reviewer_user.c.name.label("reviewer_name"),
            reviewer_user.c.surname.label("reviewer_surname"),
            reviewer_user.c.default_salary.label("reviewer_default_salary"),
        )
        .select_from(
            compensation_mistake
            .join(employee_user, compensation_mistake.c.employee_id == employee_user.c.id)
            .outerjoin(reviewer_user, compensation_mistake.c.reviewer_id == reviewer_user.c.id)
        )
        .order_by(compensation_mistake.c.incident_date.desc(), compensation_mistake.c.id.desc())
    )
    if conditions:
        query = query.where(and_(*conditions))

    rows = (await session.execute(query)).fetchall()
    return {
        "items": [
            {
                "id": row.id,
                "employee_id": row.employee_id,
                "employee_full_name": f"{row.employee_name} {row.employee_surname}".strip(),
                "reviewer_id": row.reviewer_id,
                "reviewer_full_name": (
                    f"{row.reviewer_name} {row.reviewer_surname}".strip()
                    if row.reviewer_name or row.reviewer_surname
                    else None
                ),
                "project_id": row.project_id,
                "category": row.category.value,
                "severity": row.severity.value,
                "title": row.title,
                "description": row.description,
                "incident_date": row.incident_date.isoformat(),
                "reached_client": bool(row.reached_client),
                "unclear_task": bool(row.unclear_task),
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "deduction_preview": build_incident_role_preview(row),
            }
            for row in rows
        ],
        "total_count": len(rows),
    }


@router.post("/member/mistakes", summary="Create compensation mistake incident")
async def create_compensation_mistake(
    payload: CompensationMistakeCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    await ensure_member_exists(session, payload.employee_id)
    await ensure_user_exists(session, payload.reviewer_id, "Reviewer topilmadi")
    await ensure_project_exists(session, payload.project_id)

    result = await session.execute(
        insert(compensation_mistake)
        .values(
            employee_id=payload.employee_id,
            reviewer_id=payload.reviewer_id,
            project_id=payload.project_id,
            category=payload.category,
            severity=payload.severity,
            title=payload.title.strip(),
            description=payload.description.strip(),
            incident_date=payload.incident_date,
            reached_client=payload.reached_client,
            unclear_task=payload.unclear_task,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        .returning(compensation_mistake.c.id)
    )
    mistake_id = result.scalar_one()
    await session.commit()
    return {"message": "Mistake incident muvaffaqiyatli saqlandi", "mistake_id": mistake_id}


@router.put("/member/mistakes/{mistake_id}", summary="Update compensation mistake incident")
async def update_compensation_mistake(
    mistake_id: int,
    payload: CompensationMistakeUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    existing_result = await session.execute(select(compensation_mistake).where(compensation_mistake.c.id == mistake_id))
    existing_row = existing_result.fetchone()
    if not existing_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mistake incident topilmadi")

    update_data = payload.model_dump(exclude_unset=True)
    final_employee_id = update_data.get("employee_id", existing_row.employee_id)
    final_reviewer_id = update_data.get("reviewer_id", existing_row.reviewer_id)
    final_reached_client = update_data.get("reached_client", existing_row.reached_client)
    final_unclear_task = update_data.get("unclear_task", existing_row.unclear_task)
    if (final_reached_client or final_unclear_task) and final_reviewer_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reached_client yoki unclear_task bo'lsa reviewer_id majburiy",
        )

    await ensure_member_exists(session, final_employee_id)
    await ensure_user_exists(session, final_reviewer_id, "Reviewer topilmadi")
    await ensure_project_exists(session, update_data.get("project_id", existing_row.project_id))

    if "title" in update_data and update_data["title"] is not None:
        update_data["title"] = update_data["title"].strip()
    if "description" in update_data and update_data["description"] is not None:
        update_data["description"] = update_data["description"].strip()
    update_data["updated_at"] = datetime.utcnow()

    await session.execute(
        update(compensation_mistake).where(compensation_mistake.c.id == mistake_id).values(**update_data)
    )
    await session.commit()
    return {"message": "Mistake incident yangilandi", "mistake_id": mistake_id}


@router.delete("/member/mistakes/{mistake_id}", summary="Delete compensation mistake incident")
async def delete_compensation_mistake(
    mistake_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    result = await session.execute(delete(compensation_mistake).where(compensation_mistake.c.id == mistake_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mistake incident topilmadi")
    return {"message": "Mistake incident o'chirildi", "mistake_id": mistake_id}


@router.get("/member/delivery-bonuses", summary="Delivery bonuses list")
async def list_delivery_bonuses(
    year: Optional[int] = Query(default=None, ge=2020, le=2035),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    employee_id: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    creator_user = user.alias("creator_user")
    conditions = []
    if employee_id is not None:
        conditions.append(compensation_bonus.c.employee_id == employee_id)
    if year is not None:
        conditions.append(func.extract("year", compensation_bonus.c.award_date) == year)
    if month is not None:
        conditions.append(func.extract("month", compensation_bonus.c.award_date) == month)

    query = (
        select(
            compensation_bonus.c.id,
            compensation_bonus.c.employee_id,
            compensation_bonus.c.project_id,
            compensation_bonus.c.bonus_type,
            compensation_bonus.c.title,
            compensation_bonus.c.description,
            compensation_bonus.c.award_date,
            compensation_bonus.c.created_by,
            compensation_bonus.c.created_at,
            creator_user.c.name.label("creator_name"),
            creator_user.c.surname.label("creator_surname"),
        )
        .select_from(compensation_bonus.outerjoin(creator_user, compensation_bonus.c.created_by == creator_user.c.id))
        .order_by(compensation_bonus.c.award_date.desc(), compensation_bonus.c.id.desc())
    )
    if conditions:
        query = query.where(and_(*conditions))

    rows = (await session.execute(query)).fetchall()
    return {
        "items": [
            {
                "id": row.id,
                "employee_id": row.employee_id,
                "project_id": row.project_id,
                "bonus_type": row.bonus_type.value,
                "title": row.title,
                "description": row.description,
                "award_date": row.award_date.isoformat(),
                "bonus_percent": as_money(delivery_bonus_rate(row.bonus_type)),
                "bonus_amount": as_money(
                    bonus_amount_from_percent(getattr(row, "employee_default_salary", 0), delivery_bonus_rate(row.bonus_type))
                ),
                "created_by": row.created_by,
                "created_by_full_name": (
                    f"{row.creator_name} {row.creator_surname}".strip()
                    if row.creator_name or row.creator_surname
                    else None
                ),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "total_count": len(rows),
    }


@router.post("/member/delivery-bonuses", summary="Create delivery bonus record")
async def create_delivery_bonus(
    payload: DeliveryBonusCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    await ensure_member_exists(session, payload.employee_id)
    await ensure_project_exists(session, payload.project_id)

    result = await session.execute(
        insert(compensation_bonus)
        .values(
            employee_id=payload.employee_id,
            project_id=payload.project_id,
            bonus_type=payload.bonus_type,
            title=payload.title.strip(),
            description=payload.description.strip() if payload.description else None,
            award_date=payload.award_date,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        .returning(compensation_bonus.c.id)
    )
    bonus_id = result.scalar_one()
    await session.commit()
    return {"message": "Delivery bonus saqlandi", "bonus_id": bonus_id}


@router.put("/member/delivery-bonuses/{bonus_id}", summary="Update delivery bonus record")
async def update_delivery_bonus(
    bonus_id: int,
    payload: DeliveryBonusUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    existing_result = await session.execute(select(compensation_bonus).where(compensation_bonus.c.id == bonus_id))
    existing_row = existing_result.fetchone()
    if not existing_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery bonus topilmadi")

    update_data = payload.model_dump(exclude_unset=True)
    final_employee_id = update_data.get("employee_id", existing_row.employee_id)
    await ensure_member_exists(session, final_employee_id)
    await ensure_project_exists(session, update_data.get("project_id", existing_row.project_id))
    if "title" in update_data and update_data["title"] is not None:
        update_data["title"] = update_data["title"].strip()
    if "description" in update_data and update_data["description"] is not None:
        update_data["description"] = update_data["description"].strip()
    update_data["updated_at"] = datetime.utcnow()

    await session.execute(update(compensation_bonus).where(compensation_bonus.c.id == bonus_id).values(**update_data))
    await session.commit()
    return {"message": "Delivery bonus yangilandi", "bonus_id": bonus_id}


@router.delete("/member/delivery-bonuses/{bonus_id}", summary="Delete delivery bonus record")
async def delete_delivery_bonus(
    bonus_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    result = await session.execute(delete(compensation_bonus).where(compensation_bonus.c.id == bonus_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery bonus topilmadi")
    return {"message": "Delivery bonus o'chirildi", "bonus_id": bonus_id}


@router.get("/member/salary-estimate", summary="Selected month compensation estimate")
async def get_member_salary_estimate(
    employee_id: int,
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    if current_user.id != employee_id:
        await ensure_compensation_access(session, current_user)
    user_row = await ensure_member_exists(session, employee_id, "Employee topilmadi")
    return await build_member_compensation_payload(session, user_row, year, month, include_details=True)


@router.get("/member/my-salary-estimate", summary="My monthly compensation estimate")
async def get_my_salary_estimate(
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    user_row = await ensure_member_exists(session, current_user.id, "Employee topilmadi")
    return await build_member_compensation_payload(session, user_row, year, month, include_details=True)


@router.get("/member/salary-estimates", summary="Employees monthly compensation estimates")
async def get_members_salary_estimates(
    year: int = Query(..., ge=2020, le=2035),
    month: int = Query(..., ge=1, le=12),
    employee_ids: Optional[str] = Query(default=None, description="Comma-separated employee IDs. Masalan: 1,2,3"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    selected_employee_ids = parse_employee_ids(employee_ids)
    users_query = (
        select(user.c.id, user.c.name, user.c.surname, user.c.default_salary)
        .where(and_(user.c.is_active == True, member_only_filter()))  # noqa: E712
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )
    if selected_employee_ids:
        users_query = users_query.where(user.c.id.in_(selected_employee_ids))

    users_rows = [
        row for row in (await session.execute(users_query)).fetchall() if is_visible_update_member(row.name, row.surname)
    ]
    employees = []
    total_base_salary = Decimal("0")
    total_applied_deduction = Decimal("0")
    total_bonus_amount = Decimal("0")
    total_final_salary = Decimal("0")

    for row in users_rows:
        payload = await build_member_compensation_payload(session, row, year, month, include_details=False)
        total_base_salary += normalize_base_salary(row.default_salary)
        total_applied_deduction += Decimal(str(payload["deductions_summary"]["applied_deduction_amount"]))
        total_bonus_amount += Decimal(str(payload["bonuses_summary"]["total_bonus_amount"]))
        total_final_salary += Decimal(str(payload["salary_estimate"]["final_salary"]))
        employees.append(
            {
                "employee_id": row.id,
                "full_name": f"{row.name} {row.surname}",
                "policy": payload["policy"],
                "mistakes_count": payload["mistakes_count"],
                "delivery_bonus_count": payload["delivery_bonus_count"],
                "update_productivity": payload["update_productivity"],
                "salary_estimate": payload["salary_estimate"],
            }
        )

    return {
        "period": {"year": year, "month": month, "month_name": MONTH_NUMBER_TO_UZ_NAME.get(month)},
        "filters": {"employee_ids": selected_employee_ids},
        "summary": {
            "employees_count": len(employees),
            "total_base_salary": as_money(total_base_salary),
            "total_applied_deduction_amount": as_money(total_applied_deduction),
            "total_bonus_amount": as_money(total_bonus_amount),
            "total_final_salary": as_money(total_final_salary),
            "total_estimated_salary": as_money(total_final_salary),
        },
        "employees": employees,
    }


@router.get("/member/updates/statistics", summary="Employee monthly update statistics with compensation")
async def get_employee_monthly_update_statistics(
    year: Optional[int] = Query(default=None, ge=2020, le=2035),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    employee_ids: Optional[str] = Query(default=None, description="Comma-separated user IDs. Masalan: 1,2,3"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    selected_employee_ids = parse_employee_ids(employee_ids)
    filters = []
    if year is not None:
        filters.append(monthly_update.c.year == year)
    if month is not None:
        month_values = {item.lower() for item in MONTH_FILTER_ALIASES.get(month, set())}
        filters.append(func.lower(func.trim(monthly_update.c.month)).in_(month_values))
    if selected_employee_ids:
        filters.append(monthly_update.c.user_id.in_(selected_employee_ids))

    query = (
        select(
            monthly_update.c.user_id.label("user_id"),
            user.c.name.label("name"),
            user.c.surname.label("surname"),
            user.c.default_salary.label("default_salary"),
            func.count(monthly_update.c.id).label("reports_count"),
            func.avg(monthly_update.c.update_percentage).label("avg_update_percentage"),
            func.min(monthly_update.c.update_percentage).label("min_update_percentage"),
            func.max(monthly_update.c.update_percentage).label("max_update_percentage"),
            func.sum(monthly_update.c.salary_amount).label("total_salary_amount"),
            func.max(monthly_update.c.update_date).label("latest_report_date"),
        )
        .select_from(monthly_update.join(user, monthly_update.c.user_id == user.c.id))
        .where(member_only_filter())
        .group_by(monthly_update.c.user_id, user.c.name, user.c.surname, user.c.default_salary)
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )
    if filters:
        query = query.where(and_(*filters))
    employee_rows = [
        row for row in (await session.execute(query)).fetchall() if is_visible_update_member(row.name, row.surname)
    ]

    salary_estimate_map: Dict[int, dict] = {}
    salary_summary = None
    if year is not None and month is not None:
        total_base_salary = Decimal("0")
        total_applied_deduction = Decimal("0")
        total_bonus_amount = Decimal("0")
        total_final_salary = Decimal("0")
        for row in employee_rows:
            payload = await build_member_compensation_payload(session, row, year, month, include_details=False)
            salary_estimate_map[row.user_id] = payload["salary_estimate"]
            total_base_salary += normalize_base_salary(row.default_salary)
            total_applied_deduction += Decimal(str(payload["deductions_summary"]["applied_deduction_amount"]))
            total_bonus_amount += Decimal(str(payload["bonuses_summary"]["total_bonus_amount"]))
            total_final_salary += Decimal(str(payload["salary_estimate"]["final_salary"]))
        salary_summary = {
            "total_base_salary": as_money(total_base_salary),
            "total_applied_deduction_amount": as_money(total_applied_deduction),
            "total_bonus_amount": as_money(total_bonus_amount),
            "total_final_salary": as_money(total_final_salary),
            "total_estimated_salary": as_money(total_final_salary),
        }

    total_reports = sum(int(row.reports_count or 0) for row in employee_rows)
    total_salary_amount = sum(float(row.total_salary_amount or 0) for row in employee_rows)
    weighted_percentage_sum = sum(float(row.avg_update_percentage or 0) * int(row.reports_count or 0) for row in employee_rows)
    average_update_percentage = round(weighted_percentage_sum / total_reports, 2) if total_reports else 0.0

    return {
        "filters": {"year": year, "month": month, "employee_ids": selected_employee_ids},
        "summary": {
            "total_employees": len(employee_rows),
            "total_reports": total_reports,
            "average_update_percentage": average_update_percentage,
            "total_salary_amount": round(total_salary_amount, 2),
            "salary_estimate_summary": salary_summary,
        },
        "employees": [
            {
                "user_id": row.user_id,
                "full_name": f"{row.name} {row.surname}",
                "reports_count": int(row.reports_count or 0),
                "average_update_percentage": round(float(row.avg_update_percentage or 0), 2),
                "min_update_percentage": round(float(row.min_update_percentage or 0), 2),
                "max_update_percentage": round(float(row.max_update_percentage or 0), 2),
                "total_salary_amount": round(float(row.total_salary_amount or 0), 2),
                "latest_report_date": str(row.latest_report_date) if row.latest_report_date else None,
                "salary_estimate": salary_estimate_map.get(row.user_id),
            }
            for row in employee_rows
        ],
    }


@router.post("/member/update", summary="Member uchun yangi oylik ma'lumot kiritish")
async def add_member_update(
    user_id: int,
    year: int,
    month: str,
    update_percentage: float,
    salary_amount: float,
    next_payment_date: date,
    note: str,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    await ensure_member_exists(session, user_id)
    await session.execute(
        insert(monthly_update).values(
            user_id=user_id,
            year=year,
            month=month,
            update_date=date.today(),
            update_percentage=update_percentage,
            salary_amount=salary_amount,
            next_payment_date=next_payment_date,
            note=note,
        )
    )
    await session.commit()
    return {"message": f"{month}/{year} uchun update muvaffaqiyatli qo'shildi"}


@router.get("/member/updates/all", summary="Employee update statistics with compensation by months")
async def get_all_updates(
    year: Optional[int] = Query(default=None, ge=2020, le=2035),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    employee_ids: Optional[str] = Query(default=None, description="Comma-separated user IDs. Masalan: 1,2,3"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    selected_employee_ids = parse_employee_ids(employee_ids)
    employees_query = (
        select(user.c.id, user.c.name, user.c.surname, user.c.default_salary)
        .where(and_(user.c.is_active == True, member_only_filter()))  # noqa: E712
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )
    if selected_employee_ids:
        employees_query = employees_query.where(user.c.id.in_(selected_employee_ids))
    employee_rows = [
        row
        for row in (await session.execute(employees_query)).fetchall()
        if is_visible_update_member(row.name, row.surname)
    ]
    if not employee_rows:
        return {
            "filters": {"year": year, "month": month, "employee_ids": selected_employee_ids},
            "summary": {"employees_count": 0, "periods_count": 0, "total_reports": 0, "average_update_percentage": 0.0},
            "employees": [],
        }

    employee_id_list = [row.id for row in employee_rows]
    updates_query = (
        select(
            monthly_update.c.user_id,
            monthly_update.c.year,
            monthly_update.c.month,
            monthly_update.c.update_percentage,
            monthly_update.c.salary_amount,
            monthly_update.c.update_date,
        )
        .where(monthly_update.c.user_id.in_(employee_id_list))
    )
    if year is not None:
        updates_query = updates_query.where(monthly_update.c.year == year)
    if month is not None:
        month_values = {item.lower() for item in MONTH_FILTER_ALIASES.get(month, set())}
        updates_query = updates_query.where(func.lower(func.trim(monthly_update.c.month)).in_(month_values))
    update_rows = (await session.execute(updates_query)).fetchall()

    mistakes_query = (
        select(compensation_mistake.c.employee_id, compensation_mistake.c.reviewer_id, compensation_mistake.c.incident_date)
        .where(or_(compensation_mistake.c.employee_id.in_(employee_id_list), compensation_mistake.c.reviewer_id.in_(employee_id_list)))
    )
    if year is not None:
        mistakes_query = mistakes_query.where(func.extract("year", compensation_mistake.c.incident_date) == year)
    if month is not None:
        mistakes_query = mistakes_query.where(func.extract("month", compensation_mistake.c.incident_date) == month)
    mistake_rows = (await session.execute(mistakes_query)).fetchall()

    delivery_bonus_query = select(compensation_bonus.c.employee_id, compensation_bonus.c.award_date).where(
        compensation_bonus.c.employee_id.in_(employee_id_list)
    )
    if year is not None:
        delivery_bonus_query = delivery_bonus_query.where(func.extract("year", compensation_bonus.c.award_date) == year)
    if month is not None:
        delivery_bonus_query = delivery_bonus_query.where(func.extract("month", compensation_bonus.c.award_date) == month)
    delivery_bonus_rows = (await session.execute(delivery_bonus_query)).fetchall()

    monthly_updates_map = {employee_id: {} for employee_id in employee_id_list}
    for row in update_rows:
        month_number = parse_month_to_number(row.month)
        if month is not None and month_number != month:
            continue
        period_key = (int(row.year), month_number)
        monthly_updates_map[row.user_id].setdefault(
            period_key,
            {
                "reports_count": 0,
                "update_percentage_sum": Decimal("0"),
                "total_salary_amount": Decimal("0"),
                "latest_report_date": None,
            },
        )
        period_item = monthly_updates_map[row.user_id][period_key]
        period_item["reports_count"] += 1
        period_item["update_percentage_sum"] += Decimal(str(row.update_percentage or 0))
        period_item["total_salary_amount"] += Decimal(str(row.salary_amount or 0))
        if row.update_date and (period_item["latest_report_date"] is None or row.update_date > period_item["latest_report_date"]):
            period_item["latest_report_date"] = row.update_date

    employee_incident_periods = {employee_id: set() for employee_id in employee_id_list}
    for row in mistake_rows:
        period_key = (row.incident_date.year, row.incident_date.month)
        if row.employee_id in employee_incident_periods:
            employee_incident_periods[row.employee_id].add(period_key)
        if row.reviewer_id in employee_incident_periods:
            employee_incident_periods[row.reviewer_id].add(period_key)

    employee_delivery_periods = {employee_id: set() for employee_id in employee_id_list}
    for row in delivery_bonus_rows:
        employee_delivery_periods[row.employee_id].add((row.award_date.year, row.award_date.month))

    compensation_cache: Dict[tuple[int, int, int], dict] = {}

    async def get_cached_compensation(target_user_id: int, target_year: int, target_month: int, user_row):
        cache_key = (target_user_id, target_year, target_month)
        if cache_key not in compensation_cache:
            compensation_cache[cache_key] = await build_member_compensation_payload(
                session, user_row, target_year, target_month, include_details=False
            )
        return compensation_cache[cache_key]

    employees_response = []
    total_periods = 0
    total_reports = 0
    total_update_percentage_sum = Decimal("0")
    for employee in employee_rows:
        employee_periods = monthly_updates_map.get(employee.id, {})
        merged_keys = (
            set(employee_periods.keys())
            | employee_incident_periods.get(employee.id, set())
            | employee_delivery_periods.get(employee.id, set())
        )
        sorted_keys = sorted(merged_keys, key=lambda item: (item[0], item[1] if item[1] is not None else 13))
        periods_response = []
        employee_reports_count = 0
        employee_update_percentage_sum = Decimal("0")
        for period_year, period_month in sorted_keys:
            period_update = employee_periods.get((period_year, period_month))
            compensation_payload = await get_cached_compensation(employee.id, period_year, period_month, employee)
            if period_update:
                reports_count = period_update["reports_count"]
                average_update_percentage = (
                    period_update["update_percentage_sum"] / reports_count if reports_count > 0 else Decimal("0")
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
            periods_response.append(
                {
                    "year": period_year,
                    "month": period_month,
                    "month_name": MONTH_NUMBER_TO_UZ_NAME.get(period_month, str(period_month)),
                    "reports_count": reports_count,
                    "average_update_percentage": round(float(average_update_percentage), 2),
                    "total_salary_amount": as_money(total_salary_amount),
                    "mistakes_count": compensation_payload["mistakes_count"],
                    "delivery_bonus_count": compensation_payload["delivery_bonus_count"],
                    "salary_estimate": compensation_payload["salary_estimate"],
                    "latest_report_date": str(latest_report_date) if latest_report_date else None,
                }
            )

        employee_average_update = (
            employee_update_percentage_sum / employee_reports_count if employee_reports_count > 0 else Decimal("0")
        )
        total_periods += len(periods_response)
        total_reports += employee_reports_count
        total_update_percentage_sum += employee_update_percentage_sum
        employees_response.append(
            {
                "user_id": employee.id,
                "full_name": f"{employee.name} {employee.surname}",
                "summary": {
                    "periods_count": len(periods_response),
                    "total_reports": employee_reports_count,
                    "average_update_percentage": round(float(employee_average_update), 2),
                },
                "periods": periods_response,
            }
        )

    average_update_percentage = round(float(total_update_percentage_sum / total_reports), 2) if total_reports else 0.0
    return {
        "filters": {"year": year, "month": month, "employee_ids": selected_employee_ids},
        "summary": {
            "employees_count": len(employees_response),
            "periods_count": total_periods,
            "total_reports": total_reports,
            "average_update_percentage": average_update_percentage,
        },
        "employees": employees_response,
    }


@router.get("/member/updates", summary="Foydalanuvchining o'z update'larini olish")
async def get_member_updates(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    result = await session.execute(select(monthly_update).where(monthly_update.c.user_id == current_user.id))
    updates = result.fetchall()
    return [
        {
            "id": row.id,
            "year": row.year,
            "month": row.month,
            "update_date": row.update_date,
            "update_percentage": float(row.update_percentage or 0),
            "salary_amount": float(row.salary_amount or 0),
            "next_payment_date": row.next_payment_date,
            "note": row.note,
        }
        for row in updates
    ]


@router.put("/member/update/{update_id}", summary="Update'ni to'liq tahrirlash")
async def edit_update(
    update_id: int,
    year: int,
    month: str,
    update_percentage: float,
    salary_amount: float,
    next_payment_date: date,
    note: str,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    result = await session.execute(
        update(monthly_update)
        .where(monthly_update.c.id == update_id)
        .values(
            year=year,
            month=month,
            update_percentage=update_percentage,
            salary_amount=salary_amount,
            next_payment_date=next_payment_date,
            note=note,
        )
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Update topilmadi")
    return {"message": "Update muvaffaqiyatli tahrirlandi"}


@router.patch("/member/update/{update_id}", summary="Update'ni qisman yangilash")
async def patch_update(
    update_id: int,
    update_percentage: Optional[float] = None,
    salary_amount: Optional[float] = None,
    next_payment_date: Optional[date] = None,
    note: Optional[str] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Yangilanadigan maydon topilmadi")

    result = await session.execute(update(monthly_update).where(monthly_update.c.id == update_id).values(**update_data))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Update topilmadi")
    return {"message": "Update ma'lumotlari yangilandi"}


@router.delete("/member/update/{update_id}", summary="Update'ni o'chirish")
async def delete_update(
    update_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_compensation_access(session, current_user)
    result = await session.execute(delete(monthly_update).where(monthly_update.c.id == update_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Update topilmadi")
    return {"message": "Update muvaffaqiyatli o'chirildi"}
