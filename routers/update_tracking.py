"""
Update Tracking Router
Automatic daily update tracking from Telegram channel
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, insert, update as sql_update
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List
from pydantic import BaseModel

from database import get_async_session
from auth_utils.auth_func import get_current_active_user
from models.admin_models import (
    daily_update_log, department, user_department,
    missed_update_notification, update_config
)
from models.user_models import user
from utils.update_parser import (
    parse_update_message,
    find_user_by_telegram_username,
    validate_update_content
)


router = APIRouter(prefix="/update-tracking", tags=["Update Tracking"])


# ========================================
# PYDANTIC MODELS
# ========================================

class TelegramWebhookPayload(BaseModel):
    """Telegram webhook payload"""
    message_id: str
    chat_id: str
    text: str
    from_user: Optional[str] = None
    date: Optional[int] = None


class UpdateStats(BaseModel):
    """Update statistics for a user"""
    user_id: int
    user_name: str
    total_updates: int
    updates_this_week: int
    updates_last_week: int
    updates_this_month: int
    updates_last_month: int
    updates_last_3_months: int
    percentage_this_week: float
    percentage_last_week: float
    percentage_this_month: float
    percentage_last_3_months: float
    expected_updates_per_week: int


class DepartmentStats(BaseModel):
    """Department-wide statistics"""
    department_id: int
    department_name: str
    total_employees: int
    active_employees: int
    total_updates_this_week: int
    avg_percentage_this_week: float
    avg_percentage_last_week: float
    avg_percentage_this_month: float


class CompanyStats(BaseModel):
    """Company-wide statistics"""
    total_employees: int
    total_updates_today: int
    total_updates_this_week: int
    avg_percentage_this_week: float
    avg_percentage_last_week: float
    avg_percentage_this_month: float
    avg_percentage_last_3_months: float


# ========================================
# HELPER FUNCTIONS
# ========================================

def get_date_ranges():
    """Calculate date ranges for statistics"""
    today = date.today()

    # This week (Monday to Sunday)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    # Last week
    last_week_start = week_start - timedelta(days=7)
    last_week_end = last_week_start + timedelta(days=6)

    # This month
    month_start = date(today.year, today.month, 1)

    # Last month
    if today.month == 1:
        last_month_start = date(today.year - 1, 12, 1)
        last_month_end = date(today.year, 1, 1) - timedelta(days=1)
    else:
        last_month_start = date(today.year, today.month - 1, 1)
        last_month_end = month_start - timedelta(days=1)

    # Last 3 months
    three_months_ago = today - timedelta(days=90)

    return {
        'today': today,
        'week_start': week_start,
        'week_end': week_end,
        'last_week_start': last_week_start,
        'last_week_end': last_week_end,
        'month_start': month_start,
        'last_month_start': last_month_start,
        'last_month_end': last_month_end,
        'three_months_ago': three_months_ago
    }


async def calculate_update_percentage(
    session: AsyncSession,
    user_id: int,
    start_date: date,
    end_date: date,
    expected_per_week: int = 5
) -> float:
    """
    Calculate update percentage for a date range

    Args:
        session: Database session
        user_id: User ID
        start_date: Start date
        end_date: End date
        expected_per_week: Expected updates per week

    Returns:
        Percentage (0-100)
    """
    # Count actual updates in date range
    result = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= start_date,
                daily_update_log.c.update_date <= end_date,
                daily_update_log.c.is_valid == True
            )
        )
    )
    actual_updates = result.scalar() or 0

    # Calculate expected updates based on date range
    days = (end_date - start_date).days + 1
    weeks = days / 7.0
    expected_updates = int(weeks * expected_per_week)

    if expected_updates == 0:
        return 0.0

    percentage = (actual_updates / expected_updates) * 100
    return min(percentage, 100.0)  # Cap at 100%


async def get_user_update_stats(
    session: AsyncSession,
    user_id: int,
    expected_per_week: int = 5
) -> UpdateStats:
    """Get comprehensive update statistics for a user"""
    dates = get_date_ranges()

    # Get user info
    user_result = await session.execute(
        select(user.c.name, user.c.surname)
        .where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")

    # Count total updates
    total_result = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.is_valid == True
            )
        )
    )
    total_updates = total_result.scalar() or 0

    # Count updates for each period
    updates_this_week = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= dates['week_start'],
                daily_update_log.c.update_date <= dates['week_end'],
                daily_update_log.c.is_valid == True
            )
        )
    )
    updates_this_week = updates_this_week.scalar() or 0

    updates_last_week = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= dates['last_week_start'],
                daily_update_log.c.update_date <= dates['last_week_end'],
                daily_update_log.c.is_valid == True
            )
        )
    )
    updates_last_week = updates_last_week.scalar() or 0

    updates_this_month = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= dates['month_start'],
                daily_update_log.c.is_valid == True
            )
        )
    )
    updates_this_month = updates_this_month.scalar() or 0

    updates_last_month = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= dates['last_month_start'],
                daily_update_log.c.update_date <= dates['last_month_end'],
                daily_update_log.c.is_valid == True
            )
        )
    )
    updates_last_month = updates_last_month.scalar() or 0

    updates_last_3_months = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= dates['three_months_ago'],
                daily_update_log.c.is_valid == True
            )
        )
    )
    updates_last_3_months = updates_last_3_months.scalar() or 0

    # Calculate percentages
    perc_this_week = await calculate_update_percentage(
        session, user_id, dates['week_start'], dates['week_end'], expected_per_week
    )
    perc_last_week = await calculate_update_percentage(
        session, user_id, dates['last_week_start'], dates['last_week_end'], expected_per_week
    )
    perc_this_month = await calculate_update_percentage(
        session, user_id, dates['month_start'], dates['today'], expected_per_week
    )
    perc_last_3_months = await calculate_update_percentage(
        session, user_id, dates['three_months_ago'], dates['today'], expected_per_week
    )

    return UpdateStats(
        user_id=user_id,
        user_name=f"{user_data.name} {user_data.surname}",
        total_updates=total_updates,
        updates_this_week=updates_this_week,
        updates_last_week=updates_last_week,
        updates_this_month=updates_this_month,
        updates_last_month=updates_last_month,
        updates_last_3_months=updates_last_3_months,
        percentage_this_week=round(perc_this_week, 1),
        percentage_last_week=round(perc_last_week, 1),
        percentage_this_month=round(perc_this_month, 1),
        percentage_last_3_months=round(perc_last_3_months, 1),
        expected_updates_per_week=expected_per_week
    )


# ========================================
# ENDPOINTS
# ========================================

@router.post("/telegram-webhook", summary="Telegram bot webhook")
async def telegram_webhook(
    payload: TelegramWebhookPayload,
    session: AsyncSession = Depends(get_async_session)
):
    """
    Webhook endpoint for Telegram bot to send update messages
    Parses the message and stores in database
    """
    # Parse the message
    parsed = parse_update_message(payload.text)

    if not parsed:
        return {"status": "ignored", "reason": "Invalid format"}

    # Find user by telegram username
    user_id = await find_user_by_telegram_username(
        session,
        parsed['telegram_username']
    )

    if not user_id:
        return {
            "status": "ignored",
            "reason": f"User not found for telegram username: {parsed['telegram_username']}"
        }

    # Validate content
    is_valid = validate_update_content(parsed['update_content'])

    # Check if update for this date already exists
    existing = await session.execute(
        select(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date == parsed['update_date']
            )
        )
    )
    existing_update = existing.fetchone()

    if existing_update:
        # Update existing record
        await session.execute(
            sql_update(daily_update_log)
            .where(daily_update_log.c.id == existing_update.id)
            .values(
                update_content=parsed['update_content'],
                telegram_message_id=payload.message_id,
                is_valid=is_valid,
                parsed_at=datetime.now()
            )
        )
    else:
        # Insert new record
        await session.execute(
            insert(daily_update_log).values(
                user_id=user_id,
                telegram_username=parsed['telegram_username'],
                update_date=parsed['update_date'],
                update_content=parsed['update_content'],
                telegram_message_id=payload.message_id,
                is_valid=is_valid,
                parsed_at=datetime.now(),
                created_at=datetime.now()
            )
        )

    await session.commit()

    return {
        "status": "success",
        "user_id": user_id,
        "update_date": str(parsed['update_date']),
        "is_valid": is_valid
    }


@router.get("/stats/user/{user_id}", response_model=UpdateStats, summary="Get user update statistics")
async def get_user_stats(
    user_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get update statistics for a specific user"""
    # Only allow user to see own stats or CEO to see any stats
    if current_user.id != user_id and current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="Access denied")

    stats = await get_user_update_stats(session, user_id)
    return stats


@router.get("/stats/me", response_model=UpdateStats, summary="Get my update statistics")
async def get_my_stats(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get update statistics for current user"""
    stats = await get_user_update_stats(session, current_user.id)
    return stats


@router.get("/recent", summary="Get recent updates")
async def get_recent_updates(
    limit: int = 50,
    user_id: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get recent updates (optionally filtered by user)"""
    query = select(
        daily_update_log.c.id,
        daily_update_log.c.user_id,
        daily_update_log.c.telegram_username,
        daily_update_log.c.update_date,
        daily_update_log.c.update_content,
        daily_update_log.c.is_valid,
        daily_update_log.c.created_at,
        user.c.name,
        user.c.surname
    ).join(
        user, daily_update_log.c.user_id == user.c.id
    ).order_by(
        desc(daily_update_log.c.update_date)
    ).limit(limit)

    if user_id:
        query = query.where(daily_update_log.c.user_id == user_id)

    result = await session.execute(query)
    updates = result.fetchall()

    return [
        {
            "id": u.id,
            "user_id": u.user_id,
            "user_name": f"{u.name} {u.surname}",
            "telegram_username": u.telegram_username,
            "update_date": str(u.update_date),
            "update_content": u.update_content,
            "is_valid": u.is_valid,
            "created_at": u.created_at.isoformat()
        }
        for u in updates
    ]


@router.get("/missing", summary="Get users with missing updates")
async def get_missing_updates(
    date_check: Optional[date] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get list of users who haven't submitted updates for a specific date (default: today)"""
    if current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    check_date = date_check or date.today()

    # Get all active users
    users_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.telegram_id)
        .where(
            and_(
                user.c.is_active == True,
                user.c.role != 'Customer'  # Exclude customers
            )
        )
    )
    all_users = users_result.fetchall()

    # Get users who submitted updates for this date
    updates_result = await session.execute(
        select(daily_update_log.c.user_id)
        .where(
            and_(
                daily_update_log.c.update_date == check_date,
                daily_update_log.c.is_valid == True
            )
        )
    )
    submitted_user_ids = {row.user_id for row in updates_result.fetchall()}

    # Find missing users
    missing_users = [
        {
            "user_id": u.id,
            "name": f"{u.name} {u.surname}",
            "telegram_id": u.telegram_id
        }
        for u in all_users
        if u.id not in submitted_user_ids
    ]

    return {
        "date": str(check_date),
        "total_users": len(all_users),
        "submitted": len(submitted_user_ids),
        "missing": len(missing_users),
        "missing_users": missing_users
    }


@router.get("/company-stats", response_model=CompanyStats, summary="Get company-wide statistics")
async def get_company_stats(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get company-wide update statistics (CEO only)"""
    if current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    dates = get_date_ranges()

    # Get all active employees
    employees_result = await session.execute(
        select(user.c.id)
        .where(
            and_(
                user.c.is_active == True,
                user.c.role != 'Customer'
            )
        )
    )
    all_employee_ids = [row.id for row in employees_result.fetchall()]
    total_employees = len(all_employee_ids)

    # Count today's updates
    today_count = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.update_date == dates['today'],
                daily_update_log.c.is_valid == True
            )
        )
    )
    total_updates_today = today_count.scalar() or 0

    # Count this week's updates
    week_count = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.update_date >= dates['week_start'],
                daily_update_log.c.update_date <= dates['week_end'],
                daily_update_log.c.is_valid == True
            )
        )
    )
    total_updates_this_week = week_count.scalar() or 0

    # Calculate average percentages across all employees
    percentages_this_week = []
    percentages_last_week = []
    percentages_this_month = []
    percentages_last_3_months = []

    for emp_id in all_employee_ids:
        perc_week = await calculate_update_percentage(
            session, emp_id, dates['week_start'], dates['week_end']
        )
        perc_last_week = await calculate_update_percentage(
            session, emp_id, dates['last_week_start'], dates['last_week_end']
        )
        perc_month = await calculate_update_percentage(
            session, emp_id, dates['month_start'], dates['today']
        )
        perc_3_months = await calculate_update_percentage(
            session, emp_id, dates['three_months_ago'], dates['today']
        )

        percentages_this_week.append(perc_week)
        percentages_last_week.append(perc_last_week)
        percentages_this_month.append(perc_month)
        percentages_last_3_months.append(perc_3_months)

    avg_perc_week = sum(percentages_this_week) / len(percentages_this_week) if percentages_this_week else 0
    avg_perc_last_week = sum(percentages_last_week) / len(percentages_last_week) if percentages_last_week else 0
    avg_perc_month = sum(percentages_this_month) / len(percentages_this_month) if percentages_this_month else 0
    avg_perc_3_months = sum(percentages_last_3_months) / len(percentages_last_3_months) if percentages_last_3_months else 0

    return CompanyStats(
        total_employees=total_employees,
        total_updates_today=total_updates_today,
        total_updates_this_week=total_updates_this_week,
        avg_percentage_this_week=round(avg_perc_week, 1),
        avg_percentage_last_week=round(avg_perc_last_week, 1),
        avg_percentage_this_month=round(avg_perc_month, 1),
        avg_percentage_last_3_months=round(avg_perc_3_months, 1)
    )
