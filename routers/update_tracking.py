"""
Update Tracking Router
Automatic daily update tracking from Telegram channel
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, insert, delete, update as sql_update
from datetime import datetime, date, timedelta, time as dt_time, timezone
import asyncio
import calendar
from typing import Optional, Dict, List, Any
from collections import Counter
from pydantic import BaseModel
from telegram import Bot, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, ReactionTypeEmoji
from zoneinfo import ZoneInfo
import re

from database import get_async_session, async_session_maker
from auth_utils.auth_func import get_current_active_user
from models.admin_models import (
    daily_update_log, department, user_department,
    missed_update_notification, workday_override
)
from models.user_models import user, UserRole
from schemes.schemes_update_tracking import (
    WorkdayOverrideBulkResponse,
    WorkdayOverrideCreateRequest,
    WorkdayOverrideMemberOption,
    WorkdayOverrideResponse,
    WorkdayOverrideTargetType,
    WorkdayOverrideType,
    WorkdayOverrideUpdateRequest,
)
from utils.update_parser import (
    parse_update_message,
    find_user_by_telegram_username,
    validate_update_content
)
from utils.ai_summary import generate_update_tracking_ai_summary
from dotenv import load_dotenv
from utils.admin_stats import generate_admin_statistics
from utils.workday_overrides import (
    TARGET_TYPE_ALL,
    TARGET_TYPE_MEMBER,
    build_target_key,
    fetch_override_pack,
    get_effective_override,
    is_expected_update_day,
    list_expected_update_days,
    normalize_update_required,
    summarize_expected_days,
)
from config import UPDATE_ADMIN_PASSWORD, TELEGRAM_UPDATE_BOT_TOKEN


router = APIRouter(prefix="/update-tracking", tags=["Update Tracking"])

import os


load_dotenv()

bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)

DEFAULT_UPDATE_ACCEPT_HOUR_NEXT_DAY = 4
try:
    UPDATE_TRACKING_TIMEZONE = ZoneInfo(
        os.getenv("UPDATE_TRACKING_TIMEZONE", "Asia/Tashkent")
    )
except Exception:
    UPDATE_TRACKING_TIMEZONE = timezone(timedelta(hours=5))

UPDATE_DAILY_NOTIFY_HOUR = int(os.getenv("UPDATE_DAILY_NOTIFY_HOUR", 23))
UPDATE_DAILY_NOTIFY_MINUTE = int(os.getenv("UPDATE_DAILY_NOTIFY_MINUTE", 59))
UPDATE_BOT_SCHEDULER_INTERVAL_SECONDS = int(os.getenv("UPDATE_BOT_SCHEDULER_INTERVAL_SECONDS", 30))
UPDATE_BOT_LINK_TTL_MINUTES = int(os.getenv("UPDATE_BOT_LINK_TTL_MINUTES", 10))

_update_bot_scheduler_task: Optional[asyncio.Task] = None
_last_daily_notification_date: Optional[date] = None
_pending_telegram_id_link_chats: dict[int, datetime] = {}

MONTH_NAMES_UZ = {
    1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
    5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
    9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
}

SUMMARY_STOPWORDS = {
    "bilan", "uchun", "ham", "yana", "lekin", "yoki", "bor", "yoq", "va", "bu", "shu",
    "bugun", "kecha", "erta", "ish", "qildim", "qilin", "qilish", "update", "hisobot",
    "task", "tasklar", "work", "done", "the", "and", "for", "with", "from", "that", "this"
}

# ========================================
# PYDANTIC MODELS
# ========================================

class TelegramUser(BaseModel):
    """Telegram user info"""
    id: int
    first_name: str
    username: Optional[str] = None
    language_code: Optional[str] = None


class TelegramChat(BaseModel):
    """Telegram chat info"""
    id: int
    type: str
    title: Optional[str] = None


from pydantic import BaseModel, Field, ConfigDict

class TelegramMessage(BaseModel):
    message_id: int
    from_: TelegramUser = Field(alias="from")
    chat: TelegramChat
    date: int
    text: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class TelegramWebhookPayload(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None
    edited_message: Optional[TelegramMessage] = None


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


def is_ceo_user(current_user) -> bool:
    """Allow CEO access by role or company_code (case-insensitive)."""
    role = getattr(current_user, "role", None)
    role_name = str(getattr(role, "name", "") or "").strip().lower()
    role_value = str(getattr(role, "value", "") or "").strip().lower()
    role_plain = str(role or "").strip().lower()
    company_code = str(getattr(current_user, "company_code", "") or "").strip().lower()

    return (
        role_name == "ceo"
        or role_value == "ceo"
        or role_plain == "ceo"
        or company_code == "ceo"
    )


async def get_active_member_map(session: AsyncSession, member_ids: List[int], strict: bool = True) -> Dict[int, Any]:
    normalized_ids = sorted({int(member_id) for member_id in member_ids if member_id is not None})
    if not normalized_ids:
        return {}

    result = await session.execute(
        select(
            user.c.id,
            user.c.name,
            user.c.surname,
            user.c.telegram_id,
            user.c.role,
            user.c.is_active,
            user.c.company_code,
        )
        .where(user.c.id.in_(normalized_ids))
    )
    rows = result.fetchall()
    members = {}
    for row in rows:
        if not row.is_active:
            continue
        if row.role == UserRole.customer:
            continue
        if str(row.company_code or "").strip().lower() == "ceo" or row.role == UserRole.CEO:
            continue
        members[row.id] = row

    missing_ids = [member_id for member_id in normalized_ids if member_id not in members]
    if strict and missing_ids:
        raise HTTPException(status_code=400, detail=f"Noto'g'ri member id lar: {missing_ids}")

    return members


def _serialize_workday_override(row: Any, member_row: Any = None) -> WorkdayOverrideResponse:
    member_name = None
    if member_row is not None:
        member_name = f"{member_row.name} {member_row.surname}".strip()

    return WorkdayOverrideResponse(
        id=row.id,
        special_date=row.special_date,
        day_type=WorkdayOverrideType(row.day_type),
        title=row.title,
        note=row.note,
        target_type=WorkdayOverrideTargetType(row.target_type),
        member_id=row.user_id,
        member_name=member_name,
        workday_hours=row.workday_hours,
        update_required=bool(row.update_required),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _serialize_effective_override(row: Any) -> Optional[dict]:
    if row is None:
        return None
    return {
        "id": row.id,
        "special_date": str(row.special_date),
        "day_type": row.day_type,
        "title": row.title,
        "note": row.note,
        "target_type": row.target_type,
        "member_id": row.user_id,
        "workday_hours": float(row.workday_hours) if row.workday_hours is not None else None,
        "update_required": bool(row.update_required),
    }


async def _fetch_override_rows_with_members(
    session: AsyncSession,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[WorkdayOverrideResponse]:
    conditions = []
    if start_date is not None:
        conditions.append(workday_override.c.special_date >= start_date)
    if end_date is not None:
        conditions.append(workday_override.c.special_date <= end_date)

    query = select(workday_override)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(workday_override.c.special_date.asc(), workday_override.c.target_type.asc(), workday_override.c.id.asc())

    result = await session.execute(query)
    rows = result.fetchall()

    member_ids = [row.user_id for row in rows if row.user_id is not None]
    member_map = await get_active_member_map(session, member_ids, strict=False) if member_ids else {}

    return [_serialize_workday_override(row, member_map.get(row.user_id)) for row in rows]


def get_update_accept_hour_next_day() -> int:
    """Get cutoff hour for accepting previous day updates (default: 4)."""
    hour = os.getenv("UPDATE_ACCEPT_HOUR_NEXT_DAY", str(DEFAULT_UPDATE_ACCEPT_HOUR_NEXT_DAY))
    try:
        parsed_hour = int(hour)
    except (TypeError, ValueError):
        return DEFAULT_UPDATE_ACCEPT_HOUR_NEXT_DAY

    if 0 <= parsed_hour <= 23:
        return parsed_hour
    return DEFAULT_UPDATE_ACCEPT_HOUR_NEXT_DAY


def compute_update_deadline(update_day: date, cutoff_hour: int) -> datetime:
    """
    Accept updates for `update_day` until next day `cutoff_hour:59:59`.
    Example: update_day=2026-03-09, cutoff_hour=4 -> deadline=2026-03-10 04:59:59.
    """
    deadline_day = update_day + timedelta(days=1)
    return datetime.combine(
        deadline_day,
        dt_time(hour=cutoff_hour, minute=59, second=59),
        tzinfo=UPDATE_TRACKING_TIMEZONE
    )


def is_update_within_acceptance_window(update_day: date, submitted_at: datetime, cutoff_hour: int) -> tuple[bool, datetime]:
    deadline = compute_update_deadline(update_day, cutoff_hour)
    return submitted_at <= deadline, deadline


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_telegram_id(raw_value: str) -> str:
    return raw_value.strip().lstrip("@").lower()


def _cleanup_pending_telegram_id_link_chats() -> None:
    now_utc = datetime.now(timezone.utc)
    expired_chat_ids = []
    for chat_id, created_at in _pending_telegram_id_link_chats.items():
        if (now_utc - created_at).total_seconds() > UPDATE_BOT_LINK_TTL_MINUTES * 60:
            expired_chat_ids.append(chat_id)
    for chat_id in expired_chat_ids:
        _pending_telegram_id_link_chats.pop(chat_id, None)


def _mark_chat_waiting_for_link(chat_id: int) -> None:
    _cleanup_pending_telegram_id_link_chats()
    _pending_telegram_id_link_chats[chat_id] = datetime.now(timezone.utc)


def _is_chat_waiting_for_link(chat_id: int) -> bool:
    _cleanup_pending_telegram_id_link_chats()
    return chat_id in _pending_telegram_id_link_chats


def _clear_chat_link_wait_state(chat_id: int) -> None:
    _pending_telegram_id_link_chats.pop(chat_id, None)


def _as_telegram_chat_target(chat_id_value: str | int) -> str | int:
    if isinstance(chat_id_value, str) and chat_id_value.lstrip("-").isdigit():
        return int(chat_id_value)
    return chat_id_value


async def link_user_chat_id_by_telegram_id(
    session: AsyncSession,
    chat_id: int,
    telegram_id_text: str
) -> dict:
    normalized_telegram_id = _normalize_telegram_id(telegram_id_text)
    if not normalized_telegram_id:
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Telegram ID bo'sh bo'lmasligi kerak. Masalan: `johndoe`",
            parse_mode="Markdown"
        )
        return {"status": "error", "reason": "empty_telegram_id"}

    user_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname)
        .where(
            and_(
                func.lower(user.c.telegram_id) == normalized_telegram_id,
                user.c.is_active == True
            )
        )
    )
    user_row = user_result.fetchone()
    if not user_row:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ Bunday `telegram_id` bazada topilmadi.\n"
                "Iltimos, to'g'ri ID yuboring (masalan: `johndoe`)."
            ),
            parse_mode="Markdown"
        )
        return {"status": "error", "reason": "telegram_id_not_found"}

    await session.execute(
        sql_update(user)
        .where(user.c.id == user_row.id)
        .values(chat_id=str(chat_id))
    )
    await session.commit()

    _clear_chat_link_wait_state(chat_id)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ Ulandi: {user_row.name} {user_row.surname}\n"
            "Endi kunlik update eslatmalari shu chatga yuboriladi."
        )
    )

    return {"status": "success", "user_id": user_row.id, "chat_id": str(chat_id)}


async def process_daily_update_notifications(
    session: AsyncSession,
    target_date: Optional[date] = None
) -> dict:
    report_date = target_date or datetime.now(UPDATE_TRACKING_TIMEZONE).date()
    if report_date.weekday() == 6:
        return {
            "date": str(report_date),
            "skipped": True,
            "reason": "sunday",
            "total_users": 0,
            "sent_success": 0,
            "sent_failed": 0
        }

    users_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.chat_id)
        .where(
            and_(
                user.c.is_active == True,
                or_(user.c.role.is_(None), user.c.role != UserRole.customer),
                user.c.chat_id.is_not(None),
                user.c.chat_id != ""
            )
        )
    )
    users_with_chat = users_result.fetchall()

    if not users_with_chat:
        return {
            "date": str(report_date),
            "skipped": True,
            "reason": "no_linked_users",
            "total_users": 0,
            "sent_success": 0,
            "sent_failed": 0
        }

    user_ids = [u.id for u in users_with_chat]
    override_pack = await fetch_override_pack(session, report_date, report_date, user_ids=user_ids)
    users_expected_today = [u for u in users_with_chat if is_expected_update_day(override_pack, u.id, report_date)]

    if not users_expected_today:
        return {
            "date": str(report_date),
            "skipped": True,
            "reason": "company_day_off",
            "total_users": 0,
            "sent_success": 0,
            "sent_failed": 0
        }

    user_ids = [u.id for u in users_expected_today]
    submitted_result = await session.execute(
        select(daily_update_log.c.user_id)
        .where(
            and_(
                daily_update_log.c.update_date == report_date,
                daily_update_log.c.is_valid == True,
                daily_update_log.c.user_id.in_(user_ids)
            )
        )
    )
    submitted_user_ids = {row.user_id for row in submitted_result.fetchall()}

    existing_notif_result = await session.execute(
        select(
            missed_update_notification.c.id,
            missed_update_notification.c.user_id,
            missed_update_notification.c.notification_sent
        ).where(
            and_(
                missed_update_notification.c.missed_date == report_date,
                missed_update_notification.c.user_id.in_(user_ids)
            )
        )
    )
    existing_notif_map = {row.user_id: row for row in existing_notif_result.fetchall()}

    sent_success = 0
    sent_failed = 0

    for u in users_expected_today:
        existing_log = existing_notif_map.get(u.id)
        if existing_log and existing_log.notification_sent:
            continue

        has_update = u.id in submitted_user_ids
        if has_update:
            message_text = (
                f"✅ {report_date} sanasi uchun update berganingiz uchun rahmat!"
            )
        else:
            message_text = (
                f"⚠️ Kechirasiz, {report_date} sanasi uchun update bermadingiz."
            )

        notification_sent = False
        try:
            await bot.send_message(
                chat_id=_as_telegram_chat_target(u.chat_id),
                text=message_text
            )
            notification_sent = True
            sent_success += 1
        except Exception as exc:
            print(f"[update-tracking] daily notify error user_id={u.id}: {exc}")
            sent_failed += 1

        if existing_log:
            await session.execute(
                sql_update(missed_update_notification)
                .where(missed_update_notification.c.id == existing_log.id)
                .values(
                    notification_sent=notification_sent,
                    notified_at=_utc_now_naive()
                )
            )
        else:
            await session.execute(
                insert(missed_update_notification).values(
                    user_id=u.id,
                    missed_date=report_date,
                    notification_sent=notification_sent,
                    notified_at=_utc_now_naive()
                )
            )

    await session.commit()

    return {
        "date": str(report_date),
        "skipped": False,
        "total_users": len(users_expected_today),
        "submitted_count": len(submitted_user_ids),
        "sent_success": sent_success,
        "sent_failed": sent_failed
    }


async def _update_bot_scheduler_loop() -> None:
    global _last_daily_notification_date
    while True:
        try:
            now_local = datetime.now(UPDATE_TRACKING_TIMEZONE)
            if (
                now_local.weekday() != 6
                and now_local.hour == UPDATE_DAILY_NOTIFY_HOUR
                and now_local.minute == UPDATE_DAILY_NOTIFY_MINUTE
                and _last_daily_notification_date != now_local.date()
            ):
                async with async_session_maker() as session:
                    await process_daily_update_notifications(session, target_date=now_local.date())
                _last_daily_notification_date = now_local.date()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[update-tracking] scheduler error: {exc}")
        await asyncio.sleep(UPDATE_BOT_SCHEDULER_INTERVAL_SECONDS)


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
    result = await session.execute(
        select(daily_update_log.c.update_date).distinct()
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= start_date,
                daily_update_log.c.update_date <= end_date,
                daily_update_log.c.is_valid == True
            )
        )
    )
    override_pack = await fetch_override_pack(session, start_date, end_date, user_ids=[user_id])
    expected_workdays = list_expected_update_days(override_pack, user_id, start_date, end_date)
    expected_dates = set(expected_workdays)

    actual_updates = len(
        {
            row.update_date
            for row in result.fetchall()
            if row.update_date in expected_dates
        }
    )

    expected_updates = len(expected_workdays)

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


def normalize_month_year(month: Optional[int], year: Optional[int]) -> tuple[int, int]:
    today = date.today()
    normalized_month = month if month is not None else today.month
    normalized_year = year if year is not None else today.year

    if not (1 <= normalized_month <= 12):
        raise HTTPException(status_code=400, detail="Invalid month. Must be between 1 and 12")
    if not (2020 <= normalized_year <= 2100):
        raise HTTPException(status_code=400, detail="Invalid year")

    return normalized_year, normalized_month


def get_month_range(selected_year: int, selected_month: int) -> tuple[date, date, int]:
    total_days = calendar.monthrange(selected_year, selected_month)[1]
    first_day = date(selected_year, selected_month, 1)
    last_day = date(selected_year, selected_month, total_days)
    return first_day, last_day, total_days


def extract_top_keywords(texts: List[str], limit: int = 8) -> List[Dict[str, Any]]:
    words: List[str] = []
    for text in texts:
        if not text:
            continue
        tokens = re.findall(r"[a-zA-Z0-9']+", text.lower())
        for token in tokens:
            if token.isdigit() or len(token) < 3 or token in SUMMARY_STOPWORDS:
                continue
            words.append(token)

    counts = Counter(words)
    return [{"keyword": word, "count": count} for word, count in counts.most_common(limit)]


def calculate_workday_streaks(
    valid_update_dates: List[date],
    expected_workdays: List[date],
    first_day: date,
    last_day: date,
) -> Dict[str, int]:
    valid_set = set(valid_update_dates)
    expected_set = set(expected_workdays)
    if not valid_set or not expected_set:
        return {"longest": 0, "current": 0}

    longest = 0
    running = 0
    current_day = first_day

    while current_day <= last_day:
        if current_day in expected_set:
            if current_day in valid_set:
                running += 1
                longest = max(longest, running)
            else:
                running = 0
        current_day += timedelta(days=1)

    current = 0
    cursor = min(last_day, date.today())
    while cursor >= first_day:
        if cursor not in expected_set:
            cursor -= timedelta(days=1)
            continue
        if cursor in valid_set:
            current += 1
            cursor -= timedelta(days=1)
            continue
        break

    return {"longest": longest, "current": current}


def build_weekly_breakdown(
    valid_update_dates: List[date],
    expected_workdays: List[date],
    selected_year: int,
    selected_month: int,
) -> List[Dict[str, int]]:
    valid_set = set(valid_update_dates)
    expected_set = set(expected_workdays)
    _, _, total_days = get_month_range(selected_year, selected_month)
    total_weeks = ((total_days - 1) // 7) + 1
    breakdown: List[Dict[str, int]] = []

    for week_number in range(1, total_weeks + 1):
        week_start_day = (week_number - 1) * 7 + 1
        week_end_day = min(week_start_day + 6, total_days)
        expected_working_days = 0
        updated_days = 0

        for day in range(week_start_day, week_end_day + 1):
            current_date = date(selected_year, selected_month, day)
            if current_date not in expected_set:
                continue
            expected_working_days += 1
            if current_date in valid_set:
                updated_days += 1

        percentage = round((updated_days / expected_working_days) * 100, 1) if expected_working_days > 0 else 0.0
        breakdown.append(
            {
                "week": week_number,
                "from_day": week_start_day,
                "to_day": week_end_day,
                "working_days": expected_working_days,
                "update_days": updated_days,
                "percentage": percentage,
            }
        )

    return breakdown


def compose_dynamic_ai_summary(
    full_name: str,
    month_name: str,
    selected_year: int,
    update_percentage: float,
    update_days: int,
    working_days: int,
    missing_days: int,
    days_since_last: Optional[int],
    top_keywords: List[Dict[str, Any]],
    avg_update_length: float,
    streaks: Dict[str, int],
    last_update_content: Optional[str],
) -> str:
    if update_percentage >= 90:
        grade = "A'LO"
        action_comment = "intizom juda yaxshi saqlangan."
    elif update_percentage >= 75:
        grade = "YAXSHI"
        action_comment = "barqaror ishlash bor, biroz kuchaytirsa juda yaxshi bo'ladi."
    elif update_percentage >= 50:
        grade = "O'RTACHA"
        action_comment = "natija o'rtacha, update chastotasini oshirish kerak."
    elif update_percentage >= 25:
        grade = "PAST"
        action_comment = "update intizomi past, nazoratni kuchaytirish zarur."
    else:
        grade = "JUDA PAST"
        action_comment = "keskin yaxshilash rejasi kerak."

    if days_since_last is None:
        recency = "Oxirgi update topilmadi."
    elif days_since_last == 0:
        recency = "Oxirgi update bugun yuborilgan."
    elif days_since_last == 1:
        recency = "Oxirgi update kecha yuborilgan."
    else:
        recency = f"Oxirgi update {days_since_last} kun oldin yuborilgan."

    if top_keywords:
        keywords_text = ", ".join(k["keyword"] for k in top_keywords[:5])
        focus_line = f"Asosiy yo'nalishlar: {keywords_text}."
    else:
        focus_line = "Update matnlaridan aniq yo'nalishlar ajratib bo'lmadi."

    last_snippet = None
    if last_update_content:
        clean = " ".join(last_update_content.split())
        last_snippet = clean[:160] + ("..." if len(clean) > 160 else "")

    summary_lines = [
        f"{month_name} {selected_year} uchun {full_name} bahosi: {grade} ({update_percentage}%).",
        f"Ish kunlari: {working_days}, update berilgan kunlar: {update_days}, qolib ketgan kunlar: {missing_days}.",
        recency,
        f"O'rtacha update uzunligi: {avg_update_length} belgi.",
        f"Eng uzun streak: {streaks.get('longest', 0)} kun, joriy streak: {streaks.get('current', 0)} kun.",
        focus_line,
        f"Xulosa: {action_comment}",
    ]

    if last_snippet:
        summary_lines.append(f"Oxirgi update parchasi: {last_snippet}")

    return "\n".join(summary_lines)


async def get_user_trends_payload(
    session: AsyncSession,
    user_id: int,
    months_back: int = 6
) -> Dict[str, Any]:
    today = date.today()
    trends = []

    for i in range(months_back):
        target_month = today.month - i
        target_year = today.year
        while target_month <= 0:
            target_month += 12
            target_year -= 1

        first_day, last_day, _ = get_month_range(target_year, target_month)
        override_pack = await fetch_override_pack(session, first_day, last_day, user_ids=[user_id])
        month_summary = summarize_expected_days(override_pack, user_id, first_day, last_day)
        expected_dates = set(list_expected_update_days(override_pack, user_id, first_day, last_day))

        updates_result = await session.execute(
            select(daily_update_log.c.update_date).distinct()
            .where(
                and_(
                    daily_update_log.c.user_id == user_id,
                    daily_update_log.c.update_date >= first_day,
                    daily_update_log.c.update_date <= last_day,
                    daily_update_log.c.is_valid == True,
                )
            )
        )
        update_count = len({row.update_date for row in updates_result.fetchall() if row.update_date in expected_dates})
        working_days = month_summary["working_days"]
        percentage = round((update_count / working_days) * 100, 1) if working_days > 0 else 0
        trends.append(
            {
                "month": target_month,
                "year": target_year,
                "month_name": MONTH_NAMES_UZ[target_month],
                "working_days": working_days,
                "day_off_count": month_summary["day_off_count"],
                "short_day_count": month_summary["short_day_count"],
                "update_days": update_count,
                "percentage": percentage,
            }
        )

    trends.reverse()
    avg = round(sum(t["percentage"] for t in trends) / len(trends), 1) if trends else 0.0
    return {"trends": trends, "average_percentage": avg}


async def build_user_combined_report(
    session: AsyncSession,
    user_id: int,
    month: Optional[int] = None,
    year: Optional[int] = None,
    recent_limit: int = 10
) -> Dict[str, Any]:
    selected_year, selected_month = normalize_month_year(month, year)
    first_day, last_day, total_days = get_month_range(selected_year, selected_month)

    user_result = await session.execute(
        select(
            user.c.id, user.c.name, user.c.surname, user.c.telegram_id,
            user.c.role, user.c.is_active
        ).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")

    overall_stats = await get_user_update_stats(session, user_id)
    override_pack = await fetch_override_pack(session, first_day, last_day, user_ids=[user_id])
    month_summary = summarize_expected_days(override_pack, user_id, first_day, last_day)
    expected_workdays = list_expected_update_days(override_pack, user_id, first_day, last_day)
    expected_dates = set(expected_workdays)

    updates_result = await session.execute(
        select(
            daily_update_log.c.id,
            daily_update_log.c.update_date,
            daily_update_log.c.update_content,
            daily_update_log.c.is_valid,
            daily_update_log.c.created_at
        )
        .where(
            and_(
                daily_update_log.c.user_id == user_id,
                daily_update_log.c.update_date >= first_day,
                daily_update_log.c.update_date <= last_day
            )
        )
        .order_by(daily_update_log.c.update_date.asc(), daily_update_log.c.created_at.asc())
    )
    all_updates = updates_result.fetchall()

    valid_rows = [row for row in all_updates if bool(row.is_valid)]
    valid_dates = sorted({row.update_date for row in valid_rows if row.update_date in expected_dates})
    working_days = month_summary["working_days"]
    update_days = len(valid_dates)
    missing_days = max(working_days - update_days, 0)
    update_percentage = round((update_days / working_days) * 100, 1) if working_days > 0 else 0.0
    invalid_updates_count = len(all_updates) - len(valid_rows)

    content_lengths = [len((row.update_content or "").strip()) for row in all_updates if (row.update_content or "").strip()]
    avg_update_length = round(sum(content_lengths) / len(content_lengths), 1) if content_lengths else 0.0
    max_update_length = max(content_lengths) if content_lengths else 0

    last_row = all_updates[-1] if all_updates else None
    last_update_date = last_row.update_date if last_row else None
    last_update_content = last_row.update_content if last_row else None
    days_since_last = (date.today() - last_update_date).days if last_update_date else None

    recent_updates = [
        {
            "id": row.id,
            "update_date": str(row.update_date),
            "update_content": row.update_content,
            "is_valid": row.is_valid,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in sorted(all_updates, key=lambda x: (x.update_date, x.created_at or datetime.min), reverse=True)[:recent_limit]
    ]

    period_updates = [
        {
            "id": row.id,
            "update_date": str(row.update_date),
            "update_content": row.update_content,
            "is_valid": row.is_valid,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in all_updates
    ]

    top_keywords = extract_top_keywords([row.update_content or "" for row in valid_rows], limit=8)
    streaks = calculate_workday_streaks(valid_dates, expected_workdays, first_day, last_day)
    weekly_breakdown = build_weekly_breakdown(valid_dates, expected_workdays, selected_year, selected_month)
    trends_payload = await get_user_trends_payload(session, user_id)
    period_overrides = []
    override_dates = set(override_pack.get("global", {}).keys()) | set(
        override_pack.get("member", {}).get(user_id, {}).keys()
    )
    for override_date in sorted(override_dates):
        effective = get_effective_override(override_pack, user_id, override_date)
        if effective is None:
            continue
        period_overrides.append(_serialize_effective_override(effective))

    full_name = f"{user_data.name} {user_data.surname}"
    fallback_summary = compose_dynamic_ai_summary(
        full_name=full_name,
        month_name=MONTH_NAMES_UZ[selected_month],
        selected_year=selected_year,
        update_percentage=update_percentage,
        update_days=update_days,
        working_days=working_days,
        missing_days=missing_days,
        days_since_last=days_since_last,
        top_keywords=top_keywords,
        avg_update_length=avg_update_length,
        streaks=streaks,
        last_update_content=last_update_content
    )
    ai_summary = await generate_update_tracking_ai_summary(
        full_name=full_name,
        month=selected_month,
        year=selected_year,
        update_percentage=update_percentage,
        working_days=working_days,
        update_days=update_days,
        missing_days=missing_days,
        total_updates=len(all_updates),
        valid_updates=len(valid_rows),
        invalid_updates=invalid_updates_count,
        days_since_last=days_since_last,
        top_keywords=[item["keyword"] for item in top_keywords],
        recent_updates=[item["update_content"] or "" for item in recent_updates[:5]],
        fallback_summary=fallback_summary
    )

    overall_stats_dict = overall_stats.model_dump()
    user_role_value = getattr(user_data.role, "value", None)
    if user_role_value is None and user_data.role is not None:
        user_role_value = str(user_data.role)

    return {
        "user": {
            "id": user_data.id,
            "name": full_name,
            "telegram_id": user_data.telegram_id,
            "role": user_role_value,
            "is_active": bool(user_data.is_active),
        },
        "selected_period": {
            "month": selected_month,
            "year": selected_year,
            "month_name": MONTH_NAMES_UZ[selected_month],
            "from": str(first_day),
            "to": str(last_day),
        },
        "overall_stats": overall_stats_dict,
        "period_stats": {
            "working_days": working_days,
            "sundays_count": month_summary["sundays_count"],
            "day_off_count": month_summary["day_off_count"],
            "short_day_count": month_summary["short_day_count"],
            "total_days": total_days,
            "total_updates": len(all_updates),
            "valid_updates": len(valid_rows),
            "invalid_updates": invalid_updates_count,
            "update_days": update_days,
            "missing_days": missing_days,
            "percentage": update_percentage,
            "avg_update_length": avg_update_length,
            "max_update_length": max_update_length,
        },
        "last_update": {
            "date": str(last_update_date) if last_update_date else None,
            "content": last_update_content,
            "days_ago": days_since_last,
        },
        "recent_updates": recent_updates,
        "period_updates": period_updates,
        "period_overrides": period_overrides,
        "top_keywords": top_keywords,
        "streaks": streaks,
        "weekly_breakdown": weekly_breakdown,
        "trends": trends_payload["trends"],
        "trends_average_percentage": trends_payload["average_percentage"],
        "ai_summary": ai_summary,
        # Backward-compatible keys used by existing frontend screens
        "month": selected_month,
        "year": selected_year,
        "month_name": MONTH_NAMES_UZ[selected_month],
        "working_days": working_days,
        "sundays_count": month_summary["sundays_count"],
        "total_days": total_days,
        "statistics": {
            "update_days": update_days,
            "missing_days": missing_days,
            "percentage": update_percentage,
            "total_updates": len(all_updates),
            "valid_updates": len(valid_rows),
            "invalid_updates": invalid_updates_count,
            "day_off_count": month_summary["day_off_count"],
            "short_day_count": month_summary["short_day_count"],
        },
        # Flattened old stats fields for quick compatibility
        **overall_stats_dict,
    }


# ========================================
# ENDPOINTS
# ========================================

async def handle_admin_command(
    message: TelegramMessage,
    session: AsyncSession
) -> Optional[Dict]:
    """
    /admin command handler - Interactive bot
    1. /admin в†’ parol so'raydi
    2. Parol to'g'ri в†’ oy tanlov keyboard
    3. Oy tanlandi в†’ statistika + Excel
    """
    text = message.text.strip()

    # Check if it's /admin command
    if not text.startswith('/admin'):
        # Check if it's month selection (from keyboard)
        if await is_month_selection(text):
            return await handle_month_selection(text, message, session)
        return None

    # Parse command
    parts = text.split(maxsplit=1)

    if len(parts) == 1:
        await send_telegram_message(
            chat_id=message.chat.id,
            text="рџ”ђ *ADMIN PANEL*\n\nParolni kiriting:\n`/admin <parol>`\n\n*Misol:* `/admin admin123`",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()  # <-- SHU QATORNI QOвЂSHING
        )
        return {"status": "waiting", "reason": "Password requested"}

    provided_password = parts[1].strip()

    # Check password
    if provided_password != UPDATE_ADMIN_PASSWORD:
        await send_telegram_message(
            chat_id=message.chat.id,
            text="вќЊ Noto'g'ri parol!\n\nQaytadan urinib ko'ring: `/admin <parol>`",
            parse_mode='Markdown'
        )
        return {"status": "error", "reason": "Wrong password"}

    # Password correct - show admin dashboard with month selection
    await show_admin_dashboard(message.chat.id)

    return {"status": "success", "reason": "Admin dashboard shown"}

async def is_month_selection(text: str) -> bool:
    pattern = r"(Yanvar|Fevral|Mart|Aprel|May|Iyun|Iyul|Avgust|Sentabr|Oktabr|Noyabr|Dekabr)\s+\d{4}"
    return re.search(pattern, text) is not None



async def handle_month_selection(
    text: str,
    message: TelegramMessage,
    session: AsyncSession
) -> Dict:
    print(f"Tanlangan matn: {text}")  # Bu terminalda chiqishi kerak
    """Handle month selection from keyboard"""
    try:
        # Check for cancel button
        if "Bekor qilish" in text or text.strip() == "вќЊ Bekor qilish":
            await send_telegram_message(
                chat_id=message.chat.id,
                text="вќЊ Bekor qilindi.",
                reply_markup=ReplyKeyboardRemove()
            )
            return {"status": "cancelled", "reason": "User cancelled"}

        # Clean text - remove emoji and "(Joriy)" suffix
        clean_text = text.replace("рџ“…", "").replace("(Joriy)", "").strip()

        # Parse month from text like "Yanvar 2026"
        month_names_uz = {
            "Yanvar": 1, "Fevral": 2, "Mart": 3, "Aprel": 4,
            "May": 5, "Iyun": 6, "Iyul": 7, "Avgust": 8,
            "Sentabr": 9, "Oktabr": 10, "Noyabr": 11, "Dekabr": 12
        }

        month = None
        year = None

        for month_name, month_num in month_names_uz.items():
            if month_name in clean_text:
                month = month_num
                # Extract year from text
                year_match = re.search(r"\b(20\d{2})\b", clean_text)
                if year_match:
                    year = int(year_match.group(1))
                break

        if not month or not year:
            return {"status": "error", "reason": "Invalid month format"}

        # Display month name in Uzbek
        month_name_display = list(month_names_uz.keys())[month - 1]

        # Generate statistics
        await send_telegram_message(
            chat_id=message.chat.id,
            text=f"вЏі *{month_name_display} {year}* uchun statistika tayyorlanmoqda...\n\nBiroz kuting...",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()  # Remove keyboard
        )

        stats_message, excel_bytes = await generate_admin_statistics(session, month, year)

        # Send statistics message
        await send_telegram_message(
            chat_id=message.chat.id,
            text=stats_message,
            parse_mode='Markdown'
        )

        # Send Excel file
        if excel_bytes:
            filename = f"admin_stats_{month:02d}_{year}.xlsx"
            await send_telegram_file(
                chat_id=message.chat.id,
                file_bytes=excel_bytes,
                filename=filename,
                caption=f"рџ“Љ Excel hisobot - {month:02d}.{year}"
            )

        # Show dashboard again for new selection
        await show_admin_dashboard(message.chat.id)

        return {"status": "success", "reason": "Stats sent for selected month"}

    except Exception as e:
        await send_telegram_message(
            chat_id=message.chat.id,
            text=f"вќЊ Xato yuz berdi: {str(e)}"
        )
        return {"status": "error", "reason": str(e)}


async def show_admin_dashboard(chat_id: int):
    """Show admin dashboard with month selection keyboard"""
    today = date.today()

    # Get last 12 months
    month_buttons = []

    month_names_uz = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
        5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
        9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
    }

    # Add current month at top
    current_month_text = f"рџ“… {month_names_uz[today.month]} {today.year} (Joriy)"
    month_buttons.append([KeyboardButton(current_month_text)])

    # Generate last 12 months using proper month arithmetic
    current_year = today.year
    current_month = today.month

    for i in range(1, 13):
        # Calculate previous month
        month = current_month - i
        year = current_year

        # Handle year rollover
        while month <= 0:
            month += 12
            year -= 1

        month_text = f"{month_names_uz[month]} {year}"
        month_buttons.append([KeyboardButton(month_text)])

    # Add cancel button
    month_buttons.append([KeyboardButton("вќЊ Bekor qilish")])

    keyboard = ReplyKeyboardMarkup(
        keyboard=month_buttons,
        resize_keyboard=True,
        one_time_keyboard=False
    )

    await send_telegram_message(
        chat_id=chat_id,
        text="рџ“Љ *ADMIN DASHBOARD*\n\nвњ… Parol to'g'ri!\n\nStatistika ko'rish uchun oyni tanlang:",
        parse_mode='Markdown',
        reply_markup=keyboard
    )


async def send_telegram_message(
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup=None
):
    """
    Send message to Telegram chat
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"Error sending telegram message: {e}")


async def send_telegram_file(
    chat_id: int,
    file_bytes: bytes,
    filename: str,
    caption: Optional[str] = None
):
    """
    Send file to Telegram chat
    """
    try:
        from io import BytesIO

        file_obj = BytesIO(file_bytes)
        file_obj.name = filename

        await bot.send_document(
            chat_id=chat_id,
            document=file_obj,
            filename=filename,
            caption=caption
        )
    except Exception as e:
        print(f"Error sending telegram file: {e}")


def get_update_template_text() -> str:
    """Template shown when update message does not pass parser/validation."""
    return (
        "Update for December 16\n"
        "#username\n"
        "- Birinchi task kamida 3 ta so'z bilan\n"
        "- Ikkinchi task kamida 3 ta so'z bilan"
    )


async def set_message_reaction_safe(chat_id: int, message_id: int, emoji: str):
    """Set reaction on a Telegram message and suppress reaction errors."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)]
        )
    except Exception as e:
        print(f"Error setting message reaction: {e}")


async def _upsert_workday_override(
    session: AsyncSession,
    *,
    special_date: date,
    target_type: str,
    member_id: Optional[int],
    day_type: str,
    title: str,
    note: Optional[str],
    workday_hours,
    update_required: bool,
    created_by: int,
):
    target_key = build_target_key(target_type, member_id)
    existing_result = await session.execute(
        select(workday_override).where(
            and_(
                workday_override.c.special_date == special_date,
                workday_override.c.target_key == target_key,
            )
        )
    )
    existing = existing_result.fetchone()

    payload = {
        "special_date": special_date,
        "target_type": target_type,
        "target_key": target_key,
        "user_id": member_id,
        "day_type": day_type,
        "title": title,
        "note": note,
        "workday_hours": workday_hours,
        "update_required": update_required,
        "updated_at": datetime.utcnow(),
    }

    if existing:
        await session.execute(
            sql_update(workday_override)
            .where(workday_override.c.id == existing.id)
            .values(**payload)
        )
        override_id = existing.id
    else:
        payload["created_by"] = created_by
        payload["created_at"] = datetime.utcnow()
        result = await session.execute(insert(workday_override).values(**payload))
        override_id = result.inserted_primary_key[0]

    override_result = await session.execute(
        select(workday_override).where(workday_override.c.id == override_id)
    )
    return override_result.fetchone()


@router.get(
    "/workday-overrides/member-options",
    response_model=List[WorkdayOverrideMemberOption],
    summary="Holiday yoki short day uchun member list"
)
async def get_workday_override_member_options(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    result = await session.execute(
        select(
            user.c.id,
            user.c.name,
            user.c.surname,
            user.c.telegram_id,
            user.c.role,
            user.c.company_code,
        )
        .where(
            and_(
                user.c.is_active == True,
                or_(user.c.role.is_(None), user.c.role != UserRole.customer),
                func.lower(func.coalesce(user.c.company_code, "")) != "ceo",
            )
        )
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )

    items = []
    for row in result.fetchall():
        role_value = getattr(row.role, "value", None)
        if role_value is None and row.role is not None:
            role_value = str(row.role)
        items.append(
            WorkdayOverrideMemberOption(
                id=row.id,
                name=row.name,
                surname=row.surname,
                full_name=f"{row.name} {row.surname}".strip(),
                role=role_value,
                telegram_id=row.telegram_id,
            )
        )
    return items


@router.get(
    "/workday-overrides",
    response_model=List[WorkdayOverrideResponse],
    summary="Holiday va qisqartirilgan ish kunlarini olish"
)
async def get_workday_overrides(
    month: Optional[int] = None,
    year: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    if month is not None or year is not None:
        selected_year, selected_month = normalize_month_year(month, year)
        start_date, end_date, _ = get_month_range(selected_year, selected_month)

    return await _fetch_override_rows_with_members(session, start_date=start_date, end_date=end_date)


@router.post(
    "/workday-overrides",
    response_model=WorkdayOverrideBulkResponse,
    summary="Holiday yoki short day yozuvini yaratish yoki yangilash"
)
async def create_or_update_workday_overrides(
    payload: WorkdayOverrideCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    update_required = normalize_update_required(payload.day_type.value, payload.update_required)
    target_rows: List[Any] = []

    if payload.applies_to_all:
        target_rows.append(
            await _upsert_workday_override(
                session,
                special_date=payload.special_date,
                target_type=TARGET_TYPE_ALL,
                member_id=None,
                day_type=payload.day_type.value,
                title=payload.title,
                note=payload.note,
                workday_hours=payload.workday_hours,
                update_required=update_required,
                created_by=current_user.id,
            )
        )
    else:
        member_map = await get_active_member_map(session, payload.member_ids)
        for member_id in member_map:
            target_rows.append(
                await _upsert_workday_override(
                    session,
                    special_date=payload.special_date,
                    target_type=TARGET_TYPE_MEMBER,
                    member_id=member_id,
                    day_type=payload.day_type.value,
                    title=payload.title,
                    note=payload.note,
                    workday_hours=payload.workday_hours,
                    update_required=update_required,
                    created_by=current_user.id,
                )
            )

    await session.commit()

    member_map = await get_active_member_map(
        session,
        [row.user_id for row in target_rows if row.user_id is not None],
        strict=False,
    )

    return WorkdayOverrideBulkResponse(
        message="Workday override saqlandi",
        items=[_serialize_workday_override(row, member_map.get(row.user_id)) for row in target_rows],
    )


@router.put(
    "/workday-overrides/{override_id}",
    response_model=WorkdayOverrideResponse,
    summary="Holiday yoki short day yozuvini tahrirlash"
)
async def update_workday_override(
    override_id: int,
    payload: WorkdayOverrideUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    existing_result = await session.execute(
        select(workday_override).where(workday_override.c.id == override_id)
    )
    existing = existing_result.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Override topilmadi")

    next_target_type = payload.applies_to_all
    if next_target_type is None:
        resolved_target_type = existing.target_type
    else:
        resolved_target_type = TARGET_TYPE_ALL if next_target_type else TARGET_TYPE_MEMBER

    resolved_member_id = None
    if resolved_target_type == TARGET_TYPE_MEMBER:
        resolved_member_id = payload.member_id if payload.member_id is not None else existing.user_id
        if resolved_member_id is None:
            raise HTTPException(status_code=400, detail="member target uchun member_id majburiy")
        member_map = await get_active_member_map(session, [resolved_member_id])
    else:
        member_map = {}

    resolved_day_type = payload.day_type.value if payload.day_type is not None else existing.day_type
    resolved_title = payload.title if payload.title is not None else existing.title
    resolved_note = payload.note if payload.note is not None else existing.note
    resolved_workday_hours = payload.workday_hours if payload.workday_hours is not None else existing.workday_hours
    if resolved_day_type == WorkdayOverrideType.holiday.value and payload.workday_hours is None:
        resolved_workday_hours = None
    resolved_special_date = payload.special_date if payload.special_date is not None else existing.special_date
    update_required_input = payload.update_required
    if update_required_input is None and payload.day_type is None:
        update_required_input = existing.update_required
    resolved_update_required = normalize_update_required(
        resolved_day_type,
        update_required_input,
    )

    if resolved_day_type == WorkdayOverrideType.short_day.value and resolved_workday_hours is None:
        raise HTTPException(status_code=400, detail="short_day uchun workday_hours majburiy")

    target_key = build_target_key(resolved_target_type, resolved_member_id)
    duplicate_result = await session.execute(
        select(workday_override.c.id).where(
            and_(
                workday_override.c.special_date == resolved_special_date,
                workday_override.c.target_key == target_key,
                workday_override.c.id != override_id,
            )
        )
    )
    if duplicate_result.fetchone():
        raise HTTPException(status_code=400, detail="Bu sana va target uchun yozuv allaqachon mavjud")

    await session.execute(
        sql_update(workday_override)
        .where(workday_override.c.id == override_id)
        .values(
            special_date=resolved_special_date,
            target_type=resolved_target_type,
            target_key=target_key,
            user_id=resolved_member_id,
            day_type=resolved_day_type,
            title=resolved_title,
            note=resolved_note,
            workday_hours=resolved_workday_hours,
            update_required=resolved_update_required,
            updated_at=datetime.utcnow(),
        )
    )
    await session.commit()

    updated_result = await session.execute(
        select(workday_override).where(workday_override.c.id == override_id)
    )
    updated_row = updated_result.fetchone()
    member_row = member_map.get(updated_row.user_id) if updated_row.user_id is not None else None
    return _serialize_workday_override(updated_row, member_row)


@router.delete(
    "/workday-overrides/{override_id}",
    summary="Holiday yoki short day yozuvini o'chirish"
)
async def delete_workday_override(
    override_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    existing_result = await session.execute(
        select(workday_override.c.id).where(workday_override.c.id == override_id)
    )
    if not existing_result.fetchone():
        raise HTTPException(status_code=404, detail="Override topilmadi")

    await session.execute(delete(workday_override).where(workday_override.c.id == override_id))
    await session.commit()
    return {"message": "Workday override o'chirildi"}


@router.on_event("startup")
async def start_update_tracking_scheduler() -> None:
    global _update_bot_scheduler_task
    if _update_bot_scheduler_task is None or _update_bot_scheduler_task.done():
        _update_bot_scheduler_task = asyncio.create_task(_update_bot_scheduler_loop())


@router.on_event("shutdown")
async def stop_update_tracking_scheduler() -> None:
    global _update_bot_scheduler_task
    if _update_bot_scheduler_task and not _update_bot_scheduler_task.done():
        _update_bot_scheduler_task.cancel()
        try:
            await _update_bot_scheduler_task
        except asyncio.CancelledError:
            pass


@router.post("/process-daily-notifications", summary="Kunlik update xabarlarini majburan ishga tushirish")
async def run_daily_update_notifications(
    target_date: Optional[date] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can run this")
    stats = await process_daily_update_notifications(session=session, target_date=target_date)
    return {"status": "ok", "stats": stats}


@router.post("/telegram-webhook", summary="Telegram bot webhook")
async def telegram_webhook(
    payload: TelegramWebhookPayload,
    session: AsyncSession = Depends(get_async_session)
):
    """
    Webhook endpoint for Telegram bot to process update messages.

    Commands:
    - /admin <password>: Get admin statistics

    Regular messages:
    - Update messages in format: "Update for <date>\\n#username\\n- task1\\n- task2"
    """
    message = payload.message or payload.edited_message
    is_edited = payload.edited_message is not None

    if not message or not message.text:
        return {"status": "ignored", "reason": "No text message"}

    text = message.text.strip()
    print("Keldi:", text)

    # 1. Commands
    if text.startswith('/'):
        if text.startswith('/admin'):
            result = await handle_admin_command(message, session)
            return result if result else {"status": "ignored"}
        if text.startswith('/start'):
            parts = text.split(maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                return await link_user_chat_id_by_telegram_id(
                    session=session,
                    chat_id=message.chat.id,
                    telegram_id_text=parts[1].strip()
                )

            _mark_chat_waiting_for_link(message.chat.id)
            await bot.send_message(
                chat_id=message.chat.id,
                text=(
                    "👋 Salom!\n"
                    "Iltimos, `telegram_id` kiriting (masalan: `johndoe`).\n\n"
                    "Yoki /start johndoe ko'rinishida ham yuborishingiz mumkin."
                ),
                parse_mode="Markdown"
            )
            return {"status": "waiting_for_telegram_id"}
        return {"status": "ignored", "reason": "Unknown command"}

    # 2. Month selection (admin keyboard)
    if await is_month_selection(text):
        return await handle_month_selection(text, message, session)

    if _is_chat_waiting_for_link(message.chat.id):
        return await link_user_chat_id_by_telegram_id(
            session=session,
            chat_id=message.chat.id,
            telegram_id_text=text
        )

    # 3. '#' bo'lmagan xabarlarga javob bermaymiz
    if '#' not in text:
        return {"status": "ignored", "reason": "No hashtag in message"}

    # 4. Parse update
    parsed = parse_update_message(text)
    if not parsed:
        await bot.send_message(
            chat_id=message.chat.id,
            text="❌ Update parserdan o'tmadi.\n\n"
                 "Shu shablonda yuboring:\n"
                 f"{get_update_template_text()}"
        )
        return {"status": "error", "reason": "parser_failed"}

    cutoff_hour = get_update_accept_hour_next_day()
    submitted_at = datetime.now(UPDATE_TRACKING_TIMEZONE)
    is_in_window, deadline_at = is_update_within_acceptance_window(
        parsed['update_date'],
        submitted_at,
        cutoff_hour
    )
    if not is_in_window:
        await bot.send_message(
            chat_id=message.chat.id,
            text=(
                "⏰ Bu sana uchun update yuborish vaqti tugagan.\n\n"
                f"Update sanasi: {parsed['update_date']}\n"
                f"Oxirgi muddat: {deadline_at.strftime('%Y-%m-%d %H:%M')} "
                f"({deadline_at.tzname() or 'local time'})\n\n"
                "Iltimos, bugungi sana uchun yangilanish yuboring."
            )
        )
        return {
            "status": "error",
            "reason": "submission_deadline_passed",
            "update_date": str(parsed['update_date']),
            "deadline_at": deadline_at.isoformat()
        }

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
    if not is_valid:
        await bot.send_message(
            chat_id=message.chat.id,
            text="❌ Update parserdan o'tmadi.\n\n"
                 "Talab: kamida 2 ta task, har biri kamida 3 ta so'z.\n\n"
                 "Shu shablonda yuboring:\n"
                 f"{get_update_template_text()}"
        )
        return {"status": "error", "reason": "validation_failed"}

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
        await session.execute(
            sql_update(daily_update_log)
            .where(daily_update_log.c.id == existing_update.id)
            .values(
                update_content=parsed['update_content'],
                telegram_message_id=str(message.message_id),
                is_valid=is_valid,
                parsed_at=datetime.now()
            )
        )
    else:
        await session.execute(
            insert(daily_update_log).values(
                user_id=user_id,
                telegram_username=parsed['telegram_username'],
                update_date=parsed['update_date'],
                update_content=parsed['update_content'],
                telegram_message_id=str(message.message_id),
                is_valid=is_valid,
                parsed_at=datetime.now(),
                created_at=datetime.now()
            )
        )

    await session.commit()

    # Telegram reaction whitelistdagi emojilarni ishlatamiz
    # (✅/☑️/❌ ko'p chatlarda Reaction_invalid beradi)
    success_reaction = "✍️" if is_edited else "👍"
    await set_message_reaction_safe(
        chat_id=message.chat.id,
        message_id=message.message_id,
        emoji=success_reaction
    )

    return {
        "status": "success",
        "user_id": user_id,
        "update_date": str(parsed['update_date']),
        "is_valid": is_valid,
        "is_edited": is_edited
    }


@router.get("/stats/user/{user_id}", summary="Get user update statistics with update texts")
async def get_user_stats(
    user_id: int,
    month: Optional[int] = None,
    year: Optional[int] = None,
    detailed: bool = True,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get update statistics for a specific user"""
    # Only allow user to see own stats or CEO to see any stats
    if current_user.id != user_id and not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    if not detailed and month is None and year is None:
        stats = await get_user_update_stats(session, user_id)
        return stats.model_dump()

    return await build_user_combined_report(
        session=session,
        user_id=user_id,
        month=month,
        year=year,
        recent_limit=20
    )


@router.get("/stats/me", summary="Get my update statistics")
async def get_my_stats(
    month: Optional[int] = None,
    year: Optional[int] = None,
    detailed: bool = True,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get update statistics for current user"""
    if not detailed and month is None and year is None:
        stats = await get_user_update_stats(session, current_user.id)
        return stats.model_dump()

    payload = await build_user_combined_report(
        session=session,
        user_id=current_user.id,
        month=month,
        year=year,
        recent_limit=15
    )
    payload["source_api"] = "/update-tracking/my-report"
    return payload


@router.get("/my-report", summary="Unified big report (stats + profile + monthly by month/year)")
async def get_my_report(
    month: Optional[int] = None,
    year: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    payload = await build_user_combined_report(
        session=session,
        user_id=current_user.id,
        month=month,
        year=year,
        recent_limit=20
    )
    payload["view"] = "my_report"
    payload["merged_from"] = [
        "/update-tracking/stats/me",
        "/update-tracking/my-profile",
        "/update-tracking/my-monthly-report"
    ]
    return payload


@router.get("/my-profile", summary="Get my complete profile with statistics")
async def get_my_profile(
    month: Optional[int] = None,
    year: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Get complete user profile with all statistics
    Returns: combined profile + monthly + stats payload
    """
    payload = await get_my_report(
        month=month,
        year=year,
        session=session,
        current_user=current_user
    )
    payload["deprecated_endpoint"] = "/update-tracking/my-profile"
    payload["use_api"] = "/update-tracking/my-report"
    return payload


@router.get("/my-monthly-report", summary="Get my monthly report with AI summary")
async def get_my_monthly_report(
    month: Optional[int] = None,
    year: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Get monthly report for current user (combined payload)
    """
    payload = await get_my_report(
        month=month,
        year=year,
        session=session,
        current_user=current_user
    )
    payload["deprecated_endpoint"] = "/update-tracking/my-monthly-report"
    payload["use_api"] = "/update-tracking/my-report"
    return payload


@router.get("/my-combined-report", summary="Get combined stats/profile/monthly report by selected month")
async def get_my_combined_report(
    month: Optional[int] = None,
    year: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    payload = await get_my_report(
        month=month,
        year=year,
        session=session,
        current_user=current_user
    )
    payload["deprecated_endpoint"] = "/update-tracking/my-combined-report"
    payload["use_api"] = "/update-tracking/my-report"
    return payload


@router.get("/my-daily-calendar", summary="Get my daily calendar for a month")
async def get_my_daily_calendar(
    month: Optional[int] = None,
    year: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Get daily calendar showing which days user submitted updates

    Query params:
    - month: Month (1-12), default is current month
    - year: Year (e.g., 2025), default is current year

    Returns: Calendar with daily status (submitted/missing/sunday)
    """

    # Default to current month/year
    today = date.today()
    if month is None:
        month = today.month
    if year is None:
        year = today.year

    # Validate
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Invalid month")
    if not (2020 <= year <= 2030):
        raise HTTPException(status_code=400, detail="Invalid year")

    # Get month range
    first_day = date(year, month, 1)
    num_days = calendar.monthrange(year, month)[1]
    last_day = date(year, month, num_days)
    override_pack = await fetch_override_pack(session, first_day, last_day, user_ids=[current_user.id])
    month_summary = summarize_expected_days(override_pack, current_user.id, first_day, last_day)
    expected_dates = set(list_expected_update_days(override_pack, current_user.id, first_day, last_day))

    # Get user's updates
    updates_result = await session.execute(
        select(
            daily_update_log.c.update_date,
            daily_update_log.c.update_content,
            daily_update_log.c.is_valid
        )
        .where(
            and_(
                daily_update_log.c.user_id == current_user.id,
                daily_update_log.c.update_date >= first_day,
                daily_update_log.c.update_date <= last_day
            )
        )
    )
    updates = {row.update_date: {"content": row.update_content, "valid": row.is_valid}
               for row in updates_result.fetchall()}

    # Build calendar
    weekday_names = {
        0: "Dushanba", 1: "Seshanba", 2: "Chorshanba",
        3: "Payshanba", 4: "Juma", 5: "Shanba", 6: "Yakshanba"
    }

    calendar_days = []
    update_count = 0

    for day in range(1, num_days + 1):
        current_date = date(year, month, day)
        weekday = current_date.weekday()

        day_info = {
            "day": day,
            "date": str(current_date),
            "weekday": weekday_names[weekday],
            "is_sunday": weekday == 6,
            "is_day_off": current_date.weekday() != 6 and current_date not in expected_dates,
            "update_expected": current_date in expected_dates,
            "has_update": current_date in updates
        }

        effective_override = get_effective_override(override_pack, current_user.id, current_date)
        day_info["workday_override"] = _serialize_effective_override(effective_override)

        if current_date in updates:
            update_data = updates[current_date]
            day_info["update_content"] = update_data["content"]
            day_info["is_valid"] = update_data["valid"]
            if current_date in expected_dates:
                update_count += 1
        else:
            day_info["update_content"] = None
            day_info["is_valid"] = None

        calendar_days.append(day_info)

    working_days = month_summary["working_days"]
    percentage = round((update_count / working_days) * 100, 1) if working_days > 0 else 0

    return {
        "month": month,
        "year": year,
        "working_days": working_days,
        "sundays_count": month_summary["sundays_count"],
        "day_off_count": month_summary["day_off_count"],
        "short_day_count": month_summary["short_day_count"],
        "total_days": num_days,
        "update_days": update_count,
        "missing_days": working_days - update_count,
        "percentage": percentage,
        "calendar": calendar_days
    }


@router.get("/my-trends", summary="Get my performance trends")
async def get_my_trends(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Get performance trends for last 6 months
    Shows monthly statistics to track progress over time
    """
    today = date.today()
    trends = []

    # Get last 6 months
    for i in range(6):
        # Calculate month
        target_month = today.month - i
        target_year = today.year

        while target_month <= 0:
            target_month += 12
            target_year -= 1

        # Get month range
        first_day = date(target_year, target_month, 1)
        num_days = calendar.monthrange(target_year, target_month)[1]
        last_day = date(target_year, target_month, num_days)
        override_pack = await fetch_override_pack(session, first_day, last_day, user_ids=[current_user.id])
        month_summary = summarize_expected_days(override_pack, current_user.id, first_day, last_day)
        expected_dates = set(list_expected_update_days(override_pack, current_user.id, first_day, last_day))

        # Count updates
        updates_result = await session.execute(
            select(daily_update_log.c.update_date).distinct()
            .where(
                and_(
                    daily_update_log.c.user_id == current_user.id,
                    daily_update_log.c.update_date >= first_day,
                    daily_update_log.c.update_date <= last_day,
                    daily_update_log.c.is_valid == True,
                )
            )
        )
        update_count = len({row.update_date for row in updates_result.fetchall() if row.update_date in expected_dates})
        working_days = month_summary["working_days"]

        percentage = round((update_count / working_days) * 100, 1) if working_days > 0 else 0

        month_names_uz = {
            1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
            5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
            9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
        }

        trends.append({
            "month": target_month,
            "year": target_year,
            "month_name": month_names_uz[target_month],
            "working_days": working_days,
            "day_off_count": month_summary["day_off_count"],
            "short_day_count": month_summary["short_day_count"],
            "update_days": update_count,
            "percentage": percentage
        })

    # Reverse to show oldest to newest
    trends.reverse()

    return {
        "trends": trends,
        "average_percentage": round(sum(t["percentage"] for t in trends) / len(trends), 1) if trends else 0
    }


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


@router.get("/employee-monthly-updates", summary="Get employee monthly updates with message texts")
async def get_employee_monthly_updates(
    employee_id: int,
    year: int,
    month: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """CEO endpoint: get all update messages for an employee in a given year/month."""
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail="year must be between 2000 and 2100")
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month must be between 1 and 12")

    employee_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.is_active)
        .where(user.c.id == employee_id)
    )
    employee = employee_result.fetchone()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    updates_result = await session.execute(
        select(
            daily_update_log.c.id,
            daily_update_log.c.update_date,
            daily_update_log.c.update_content,
            daily_update_log.c.is_valid,
            daily_update_log.c.created_at
        )
        .where(
            and_(
                daily_update_log.c.user_id == employee_id,
                daily_update_log.c.update_date >= month_start,
                daily_update_log.c.update_date <= month_end
            )
        )
        .order_by(daily_update_log.c.update_date.asc(), daily_update_log.c.created_at.asc())
    )
    rows = updates_result.fetchall()

    return {
        "employee": {
            "id": employee.id,
            "full_name": f"{employee.name} {employee.surname}",
            "is_active": bool(employee.is_active),
        },
        "period": {
            "year": year,
            "month": month,
            "from": str(month_start),
            "to": str(month_end),
        },
        "total_updates": len(rows),
        "updates": [
            {
                "id": row.id,
                "update_date": str(row.update_date),
                "update_content": row.update_content,
                "is_valid": row.is_valid,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@router.get("/missing", summary="Get users with missing updates")
async def get_missing_updates(
    date_check: Optional[date] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """Get list of users who haven't submitted updates for a specific date (default: today)"""
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    check_date = date_check or date.today()

    # Get all active users
    users_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.telegram_id)
        .where(
            and_(
                user.c.is_active == True,
                or_(user.c.role.is_(None), user.c.role != UserRole.customer)
            )
        )
    )
    all_users = users_result.fetchall()
    eligible_users = all_users
    if check_date.weekday() != 6 and all_users:
        override_pack = await fetch_override_pack(session, check_date, check_date, user_ids=[u.id for u in all_users])
        eligible_users = [u for u in all_users if is_expected_update_day(override_pack, u.id, check_date)]
    else:
        eligible_users = []

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
    eligible_user_ids = {u.id for u in eligible_users}
    submitted_user_ids = {row.user_id for row in updates_result.fetchall() if row.user_id in eligible_user_ids}

    # Find missing users
    missing_users = [
        {
            "user_id": u.id,
            "name": f"{u.name} {u.surname}",
            "telegram_id": u.telegram_id
        }
        for u in eligible_users
        if u.id not in submitted_user_ids
    ]

    return {
        "date": str(check_date),
        "total_users": len(eligible_users),
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
    if not is_ceo_user(current_user):
        raise HTTPException(status_code=403, detail="Only CEO can access this")

    dates = get_date_ranges()

    # Get all active employees
    employees_result = await session.execute(
        select(user.c.id)
        .where(
            and_(
                user.c.is_active == True

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
