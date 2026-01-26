"""
Update Tracking Router
Automatic daily update tracking from Telegram channel
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, insert, update as sql_update
from datetime import datetime, date, timedelta
import calendar
from typing import Optional, Dict, List
from pydantic import BaseModel
from telegram import Bot, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

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
from telegram import Bot
from dotenv import load_dotenv
from utils.admin_stats import generate_admin_statistics
from config import UPDATE_ADMIN_PASSWORD, TELEGRAM_UPDATE_BOT_TOKEN


router = APIRouter(prefix="/update-tracking", tags=["Update Tracking"])

import os


load_dotenv()

bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)

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

async def handle_admin_command(
    message: TelegramMessage,
    session: AsyncSession
) -> Optional[Dict]:
    """
    /admin command handler - Interactive bot
    1. /admin → parol so'raydi
    2. Parol to'g'ri → oy tanlov keyboard
    3. Oy tanlandi → statistika + Excel
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
            text="🔐 *ADMIN PANEL*\n\nParolni kiriting:\n`/admin <parol>`\n\n*Misol:* `/admin admin123`",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()  # <-- SHU QATORNI QO‘SHING
        )
        return {"status": "waiting", "reason": "Password requested"}

    provided_password = parts[1].strip()

    # Check password
    if provided_password != UPDATE_ADMIN_PASSWORD:
        await send_telegram_message(
            chat_id=message.chat.id,
            text="❌ Noto'g'ri parol!\n\nQaytadan urinib ko'ring: `/admin <parol>`",
            parse_mode='Markdown'
        )
        return {"status": "error", "reason": "Wrong password"}

    # Password correct - show admin dashboard with month selection
    await show_admin_dashboard(message.chat.id)

    return {"status": "success", "reason": "Admin dashboard shown"}

import re
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
        if "Bekor qilish" in text or text.strip() == "❌ Bekor qilish":
            await send_telegram_message(
                chat_id=message.chat.id,
                text="❌ Bekor qilindi.",
                reply_markup=ReplyKeyboardRemove()
            )
            return {"status": "cancelled", "reason": "User cancelled"}

        # Clean text - remove emoji and "(Joriy)" suffix
        clean_text = text.replace("📅", "").replace("(Joriy)", "").strip()

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
            text=f"⏳ *{month_name_display} {year}* uchun statistika tayyorlanmoqda...\n\nBiroz kuting...",
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
                caption=f"📊 Excel hisobot - {month:02d}.{year}"
            )

        # Show dashboard again for new selection
        await show_admin_dashboard(message.chat.id)

        return {"status": "success", "reason": "Stats sent for selected month"}

    except Exception as e:
        await send_telegram_message(
            chat_id=message.chat.id,
            text=f"❌ Xato yuz berdi: {str(e)}"
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
    current_month_text = f"📅 {month_names_uz[today.month]} {today.year} (Joriy)"
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
    month_buttons.append([KeyboardButton("❌ Bekor qilish")])

    keyboard = ReplyKeyboardMarkup(
        keyboard=month_buttons,
        resize_keyboard=True,
        one_time_keyboard=False
    )

    await send_telegram_message(
        chat_id=chat_id,
        text="📊 *ADMIN DASHBOARD*\n\n✅ Parol to'g'ri!\n\nStatistika ko'rish uchun oyni tanlang:",
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


@router.post("/telegram-webhook", summary="Telegram bot webhook")
async def telegram_webhook(
    payload: TelegramWebhookPayload,
    session: AsyncSession = Depends(get_async_session)
):



    """
    Webhook endpoint for Telegram bot to send update messages
    Receives standard Telegram webhook format and processes messages

    Commands:
    - /admin <password>: Get admin statistics

    Regular messages:
    - Update messages in format: "Update for <date>\\n#username\\n- task1\\n- task2"
    """
    # Get the message (could be regular message or edited message)
    message = payload.message or payload.edited_message

    if not message or not message.text:
        return {"status": "ignored", "reason": "No text message"}

    text = message.text.strip()

    print("Keldi:", text)

    # 1. Avval commandlar
    if text.startswith('/'):
        if text.startswith('/admin'):
            result = await handle_admin_command(message, session)
            return result if result else {"status": "ignored"}

        return {"status": "ignored", "reason": "Unknown command"}

    # 2. Keyin oy tanlash
    if await is_month_selection(text):
        return await handle_month_selection(text, message, session)

        # 3. Endi oddiy update parse
    parsed = parse_update_message(text)

    if not parsed:
        await bot.send_message(
            chat_id=message.chat.id,
            text="❌ Update parserdan o‘tmadi.\n\n"
                 "To‘g‘ri format:\n"
                 "#username\n"
                 "- Kamida 2 ta task\n"
                 "- Har biri kamida 3 so‘z"
        )
        return {"status": "error", "reason": "parser_failed"}




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
            text="❌ Update talabga mos emas.\nKamida 2 ta task va har biri 3 ta so‘z."
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
        # Update existing record
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
        # Insert new record
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
    if existing_update:
        await bot.send_message(
            chat_id=message.chat.id,
            text="♻️ Update yangilandi (eski update ustiga yozildi)."
        )
    else:
        await bot.send_message(
            chat_id=message.chat.id,
            text="✅ Update qabul qilindi."
        )
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


@router.get("/my-profile", summary="Get my complete profile with statistics")
async def get_my_profile(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Get complete user profile with all statistics
    Returns: User info, overall stats, recent updates, monthly trends
    """
    # Get user info
    user_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.telegram_id, user.c.role)
        .where(user.c.id == current_user.id)
    )
    user_data = user_result.fetchone()

    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")

    # Get overall stats
    stats = await get_user_update_stats(session, current_user.id)

    # Get total updates count
    total_result = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == current_user.id,
                daily_update_log.c.is_valid == True
            )
        )
    )
    total_updates = total_result.scalar() or 0

    # Get recent 5 updates
    recent_result = await session.execute(
        select(
            daily_update_log.c.update_date,
            daily_update_log.c.update_content,
            daily_update_log.c.is_valid
        )
        .where(daily_update_log.c.user_id == current_user.id)
        .order_by(desc(daily_update_log.c.update_date))
        .limit(5)
    )
    recent_updates = [
        {
            "date": str(row.update_date),
            "content": row.update_content[:100] + "..." if len(row.update_content) > 100 else row.update_content,
            "is_valid": row.is_valid
        }
        for row in recent_result.fetchall()
    ]

    # Get current month stats
    today = date.today()
    month_start = date(today.year, today.month, 1)

    current_month_result = await session.execute(
        select(func.count()).select_from(daily_update_log)
        .where(
            and_(
                daily_update_log.c.user_id == current_user.id,
                daily_update_log.c.update_date >= month_start,
                daily_update_log.c.is_valid == True
            )
        )
    )
    current_month_updates = current_month_result.scalar() or 0

    return {
        "user": {
            "id": user_data.id,
            "name": f"{user_data.name} {user_data.surname}",
            "telegram_id": user_data.telegram_id,
            "role": user_data.role
        },
        "statistics": {
            "total_updates": total_updates,
            "this_week": stats.updates_this_week,
            "this_month": current_month_updates,
            "percentage_this_week": stats.percentage_this_week,
            "percentage_this_month": stats.percentage_this_month,
            "percentage_last_3_months": stats.percentage_last_3_months
        },
        "recent_updates": recent_updates
    }


@router.get("/my-monthly-report", summary="Get my monthly report with AI summary")
async def get_my_monthly_report(
    month: Optional[int] = None,
    year: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Get monthly report for current user with AI summary

    Query params:
    - month: Month (1-12), default is current month
    - year: Year (e.g., 2025), default is current year

    Returns: Detailed monthly statistics with AI analysis
    """
    from utils.admin_stats import get_working_days_in_month, generate_ai_summary

    # Default to current month/year
    today = date.today()
    if month is None:
        month = today.month
    if year is None:
        year = today.year

    # Validate month/year
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Invalid month. Must be between 1 and 12")
    if not (2020 <= year <= 2030):
        raise HTTPException(status_code=400, detail="Invalid year")

    # Get working days
    working_days, sundays = get_working_days_in_month(year, month)

    # Get month range
    first_day = date(year, month, 1)
    num_days = calendar.monthrange(year, month)[1]
    last_day = date(year, month, num_days)

    # Get user's updates for this month
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
        .order_by(daily_update_log.c.update_date)
    )
    all_updates = updates_result.fetchall()

    # Filter out Sunday updates
    valid_updates = [upd for upd in all_updates if upd.update_date.weekday() != 6]

    # Calculate stats
    update_days = len(valid_updates)
    update_percentage = round((update_days / working_days) * 100, 1) if working_days > 0 else 0

    # Get last update
    last_update_date = all_updates[-1].update_date if all_updates else None
    last_update_content = all_updates[-1].update_content if all_updates else None
    days_since_last = (today - last_update_date).days if last_update_date else None

    # Get user info
    user_result = await session.execute(
        select(user.c.name, user.c.surname)
        .where(user.c.id == current_user.id)
    )
    user_data = user_result.fetchone()
    full_name = f"{user_data.name} {user_data.surname}"

    # Generate AI summary
    ai_summary = generate_ai_summary(
        full_name=full_name,
        update_days=update_days,
        total_days=working_days,
        update_percentage=update_percentage,
        last_update_content=last_update_content,
        days_since_last=days_since_last
    )

    # Month name in Uzbek
    month_names_uz = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
        5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
        9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
    }

    return {
        "month": month,
        "year": year,
        "month_name": month_names_uz[month],
        "working_days": working_days,
        "sundays_count": len(sundays),
        "total_days": num_days,
        "statistics": {
            "update_days": update_days,
            "missing_days": working_days - update_days,
            "percentage": update_percentage,
            "total_updates": len(all_updates)
        },
        "ai_summary": ai_summary,
        "last_update": {
            "date": str(last_update_date) if last_update_date else None,
            "content": last_update_content[:200] + "..." if last_update_content and len(last_update_content) > 200 else last_update_content,
            "days_ago": days_since_last
        }
    }


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
    from utils.admin_stats import get_working_days_in_month

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

    # Get working days
    working_days, sundays = get_working_days_in_month(year, month)

    # Get month range
    first_day = date(year, month, 1)
    num_days = calendar.monthrange(year, month)[1]
    last_day = date(year, month, num_days)

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
            "has_update": current_date in updates
        }

        if current_date in updates:
            update_data = updates[current_date]
            day_info["update_content"] = update_data["content"]
            day_info["is_valid"] = update_data["valid"]
            if weekday != 6:  # Not Sunday
                update_count += 1
        else:
            day_info["update_content"] = None
            day_info["is_valid"] = None

        calendar_days.append(day_info)

    percentage = round((update_count / working_days) * 100, 1) if working_days > 0 else 0

    return {
        "month": month,
        "year": year,
        "working_days": working_days,
        "sundays_count": len(sundays),
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
    from utils.admin_stats import get_working_days_in_month

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

        # Get working days
        working_days, _ = get_working_days_in_month(target_year, target_month)

        # Get month range
        first_day = date(target_year, target_month, 1)
        num_days = calendar.monthrange(target_year, target_month)[1]
        last_day = date(target_year, target_month, num_days)

        # Count updates
        updates_result = await session.execute(
            select(func.count()).select_from(daily_update_log)
            .where(
                and_(
                    daily_update_log.c.user_id == current_user.id,
                    daily_update_log.c.update_date >= first_day,
                    daily_update_log.c.update_date <= last_day,
                    # Exclude Sundays by checking weekday
                    func.extract('dow', daily_update_log.c.update_date) != 0  # 0 = Sunday in PostgreSQL
                )
            )
        )
        update_count = updates_result.scalar() or 0

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
