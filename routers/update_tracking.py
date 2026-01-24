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
from telegram import Bot, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import calendar as cal_module

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
from utils.admin_stats import generate_admin_statistics, generate_user_daily_report
from config import UPDATE_ADMIN_PASSWORD, TELEGRAM_UPDATE_BOT_TOKEN


router = APIRouter(prefix="/update-tracking", tags=["Update Tracking"])


# ========================================
# USER STATE MANAGEMENT (In-memory)
# ========================================

# State storage: {chat_id: {"state": "waiting_password"|"admin_menu"|"selecting_month"|"selecting_user", "data": {...}}}
user_states: Dict[int, Dict] = {}


def set_user_state(chat_id: int, state: str, data: Optional[Dict] = None):
    """Set user state for multi-step flows"""
    user_states[chat_id] = {
        "state": state,
        "data": data or {}
    }


def get_user_state(chat_id: int) -> Optional[Dict]:
    """Get user state"""
    return user_states.get(chat_id)


def clear_user_state(chat_id: int):
    """Clear user state"""
    if chat_id in user_states:
        del user_states[chat_id]


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


class TelegramMessage(BaseModel):
    """Telegram message"""
    message_id: int
    from_: TelegramUser
    chat: TelegramChat
    date: int
    text: Optional[str] = None

    class Config:
        # Allow 'from' as field name (it's a Python keyword)
        populate_by_name = True
        # Map 'from' to 'from_'
        fields = {'from_': 'from'}


class TelegramWebhookPayload(BaseModel):
    """Telegram webhook payload - Standard Telegram format"""
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
    /admin command handler - Interactive bot with multi-step flow
    1. /admin → parol so'raydi
    2. Parol kiritiladi → admin menu (Statistika, Userlar)
    3. Menu tanlanadi → keyingi qadamlar
    """
    # Ask for password
    set_user_state(message.chat.id, "waiting_password")

    await send_telegram_message(
        chat_id=message.chat.id,
        text="🔐 *ADMIN PANEL*\n\nIltimos, parolni kiriting:",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )

    return {"status": "waiting", "reason": "Password requested"}


async def handle_user_state(
    message: TelegramMessage,
    session: AsyncSession,
    user_state: Dict
) -> Optional[Dict]:
    """
    Handle user actions based on current state
    """
    text = message.text.strip()
    state = user_state["state"]
    data = user_state.get("data", {})

    # Cancel/Exit handling
    if text in ["❌ Bekor qilish", "❌ Chiqish"]:
        clear_user_state(message.chat.id)
        await send_telegram_message(
            chat_id=message.chat.id,
            text="❌ Bekor qilindi.",
            reply_markup=ReplyKeyboardRemove()
        )
        return {"status": "cancelled"}

    # Back button handling
    if text == "🔙 Orqaga":
        # Go back to previous state
        if state == "selecting_month":
            if data.get("context") == "user_report":
                # Go back to user list
                await show_user_list(message.chat.id, session)
            else:
                # Go back to admin menu
                await show_admin_menu(message.chat.id)
        elif state == "selecting_user":
            await show_admin_menu(message.chat.id)
        elif state == "selecting_format":
            await show_month_selection(message.chat.id, data.get("context", "statistics"))
        return {"status": "back"}

    # State-specific handling
    if state == "waiting_password":
        return await handle_password_input(message, text)

    elif state == "admin_menu":
        return await handle_admin_menu_selection(message, session, text)

    elif state == "selecting_month":
        return await handle_month_selection_new(message, session, text, data)

    elif state == "selecting_user":
        return await handle_user_selection(message, session, text)

    elif state == "selecting_format":
        return await handle_format_selection(message, session, text, data)

    return None


async def handle_password_input(message: TelegramMessage, password: str) -> Dict:
    """Handle password input"""
    if password != UPDATE_ADMIN_PASSWORD:
        await send_telegram_message(
            chat_id=message.chat.id,
            text="❌ Noto'g'ri parol!\n\nIltimos, qaytadan kiriting:",
            parse_mode='Markdown'
        )
        return {"status": "error", "reason": "Wrong password"}

    # Password correct - show admin menu
    await show_admin_menu(message.chat.id)
    return {"status": "success", "reason": "Password accepted"}


async def handle_admin_menu_selection(
    message: TelegramMessage,
    session: AsyncSession,
    text: str
) -> Dict:
    """Handle admin menu button selection"""
    if text == "📊 Statistika":
        # Show month selection for statistics
        await show_month_selection(message.chat.id, context="statistics")
        return {"status": "navigating", "target": "statistics"}

    elif text == "👥 Userlar":
        # Show user list
        await show_user_list(message.chat.id, session)
        return {"status": "navigating", "target": "users"}

    return {"status": "ignored"}


async def show_user_list(chat_id: int, session: AsyncSession):
    """Show list of users for selection"""
    # Get all active users
    result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.telegram_id)
        .where(user.c.is_active == True)
        .order_by(user.c.name)
    )
    users = result.fetchall()

    if not users:
        await send_telegram_message(
            chat_id=chat_id,
            text="❌ Faol foydalanuvchilar topilmadi.",
            reply_markup=ReplyKeyboardRemove()
        )
        clear_user_state(chat_id)
        return

    # Create keyboard with user buttons
    user_buttons = []
    for u in users:
        full_name = f"{u.name} {u.surname}"
        user_buttons.append([KeyboardButton(full_name)])

    # Add back and cancel buttons
    user_buttons.append([KeyboardButton("🔙 Orqaga"), KeyboardButton("❌ Bekor qilish")])

    keyboard = ReplyKeyboardMarkup(
        keyboard=user_buttons,
        resize_keyboard=True,
        one_time_keyboard=False
    )

    set_user_state(chat_id, "selecting_user", {"users": [{"id": u.id, "name": f"{u.name} {u.surname}"} for u in users]})

    await send_telegram_message(
        chat_id=chat_id,
        text="👤 *Foydalanuvchini tanlang:*",
        parse_mode='Markdown',
        reply_markup=keyboard
    )


async def handle_user_selection(
    message: TelegramMessage,
    session: AsyncSession,
    text: str
) -> Dict:
    """Handle user selection from list"""
    user_state = get_user_state(message.chat.id)
    users_list = user_state.get("data", {}).get("users", [])

    # Find selected user
    selected_user = None
    for u in users_list:
        if u["name"] == text:
            selected_user = u
            break

    if not selected_user:
        return {"status": "ignored"}

    # Show month selection for this user
    set_user_state(message.chat.id, "selecting_month", {
        "context": "user_report",
        "user_id": selected_user["id"],
        "user_name": selected_user["name"]
    })

    await show_month_selection(message.chat.id, context="user_report")
    return {"status": "user_selected", "user_id": selected_user["id"]}


async def is_month_selection(text: str) -> bool:
    """Check if message is month selection from keyboard"""
    # Check for cancel button
    if "Bekor qilish" in text or text.strip() == "❌ Bekor qilish":
        return False

    month_names_uz = [
        "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
        "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"
    ]

    for month_name in month_names_uz:
        if month_name in text and any(char.isdigit() for char in text):
            return True
    return False


async def handle_month_selection_new(
    message: TelegramMessage,
    session: AsyncSession,
    text: str,
    data: Dict
) -> Dict:
    """Handle month selection - new version with state management"""
    # Check for cancel button
    if "Bekor qilish" in text:
        clear_user_state(message.chat.id)
        await send_telegram_message(
            chat_id=message.chat.id,
            text="❌ Bekor qilindi.",
            reply_markup=ReplyKeyboardRemove()
        )
        return {"status": "cancelled"}

    # Parse month
    if not await is_month_selection(text):
        return {"status": "ignored"}

    # Clean text
    clean_text = text.replace("📅", "").replace("(Joriy)", "").strip()

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
            year_match = [int(s) for s in clean_text.split() if s.isdigit()]
            if year_match:
                year = year_match[0]
            break

    if not month or not year:
        return {"status": "error", "reason": "Invalid month format"}

    context = data.get("context", "statistics")

    if context == "statistics":
        # Show format selection (text or excel)
        await show_format_selection(message.chat.id, month, year)
        return {"status": "month_selected", "month": month, "year": year}

    elif context == "user_report":
        # Generate user report directly (only excel)
        user_id = data.get("user_id")
        user_name = data.get("user_name")

        await send_telegram_message(
            chat_id=message.chat.id,
            text=f"⏳ *{user_name}* uchun hisobot tayyorlanmoqda...\n\nBiroz kuting...",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
        )

        # Generate user daily report
        excel_bytes = await generate_user_daily_report(session, user_id, month, year)

        if excel_bytes:
            month_name_display = list(month_names_uz.keys())[month - 1]
            filename = f"user_report_{user_id}_{month:02d}_{year}.xlsx"
            await send_telegram_file(
                chat_id=message.chat.id,
                file_bytes=excel_bytes,
                filename=filename,
                caption=f"📊 {user_name} - {month_name_display} {year} hisoboti"
            )

        # Show admin menu again
        await show_admin_menu(message.chat.id)
        return {"status": "report_sent"}

    return {"status": "ignored"}


async def show_format_selection(chat_id: int, month: int, year: int):
    """Show format selection (text or excel)"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📝 Text"), KeyboardButton("📊 Excel")],
            [KeyboardButton("📦 Ikkalasi ham")],
            [KeyboardButton("🔙 Orqaga"), KeyboardButton("❌ Bekor qilish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    set_user_state(chat_id, "selecting_format", {
        "month": month,
        "year": year,
        "context": "statistics"
    })

    await send_telegram_message(
        chat_id=chat_id,
        text="📤 *Format tanlang:*\n\nStatistikani qanday ko'rinishda olishni xohlaysiz?",
        parse_mode='Markdown',
        reply_markup=keyboard
    )


async def handle_format_selection(
    message: TelegramMessage,
    session: AsyncSession,
    text: str,
    data: Dict
) -> Dict:
    """Handle format selection"""
    month = data.get("month")
    year = data.get("year")

    if not month or not year:
        return {"status": "error", "reason": "Missing month/year"}

    month_names_uz = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
        5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
        9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
    }
    month_name_display = month_names_uz[month]

    send_text = text in ["📝 Text", "📦 Ikkalasi ham"]
    send_excel = text in ["📊 Excel", "📦 Ikkalasi ham"]

    if not send_text and not send_excel:
        return {"status": "ignored"}

    await send_telegram_message(
        chat_id=message.chat.id,
        text=f"⏳ *{month_name_display} {year}* uchun statistika tayyorlanmoqda...\n\nBiroz kuting...",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )

    # Generate statistics
    stats_message, excel_bytes = await generate_admin_statistics(session, month, year)

    # Send based on selection
    if send_text:
        await send_telegram_message(
            chat_id=message.chat.id,
            text=stats_message,
            parse_mode='Markdown'
        )

    if send_excel and excel_bytes:
        filename = f"admin_stats_{month:02d}_{year}.xlsx"
        await send_telegram_file(
            chat_id=message.chat.id,
            file_bytes=excel_bytes,
            filename=filename,
            caption=f"📊 Excel hisobot - {month:02d}.{year}"
        )

    # Show admin menu again
    await show_admin_menu(message.chat.id)

    return {"status": "stats_sent"}


async def show_admin_menu(chat_id: int):
    """Show admin main menu with Statistika and Userlar options"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📊 Statistika"), KeyboardButton("👥 Userlar")],
            [KeyboardButton("❌ Chiqish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    set_user_state(chat_id, "admin_menu")

    await send_telegram_message(
        chat_id=chat_id,
        text="✅ *ADMIN PANEL*\n\nXush kelibsiz! Quyidagilardan birini tanlang:",
        parse_mode='Markdown',
        reply_markup=keyboard
    )


async def show_month_selection(chat_id: int, context: str = "statistics"):
    """
    Show month selection keyboard
    context: 'statistics' or 'user_report'
    """
    today = date.today()
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

    # Add back and cancel buttons
    month_buttons.append([KeyboardButton("🔙 Orqaga"), KeyboardButton("❌ Bekor qilish")])

    keyboard = ReplyKeyboardMarkup(
        keyboard=month_buttons,
        resize_keyboard=True,
        one_time_keyboard=False
    )

    set_user_state(chat_id, "selecting_month", {"context": context})

    text = "📅 *Oyni tanlang:*"
    if context == "user_report":
        text = "📅 *Qaysi oy uchun hisobot kerak?*"

    await send_telegram_message(
        chat_id=chat_id,
        text=text,
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
        bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)
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

        bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)
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

    # Check if it's a command
    if message.text.startswith('/'):
        # Handle admin command
        if message.text.startswith('/admin'):
            result = await handle_admin_command(message, session)
            return result if result else {"status": "ignored"}

        # Other commands can be added here
        return {"status": "ignored", "reason": "Unknown command"}

    # Check user state for multi-step flows
    user_state = get_user_state(message.chat.id)

    if user_state:
        result = await handle_user_state(message, session, user_state)
        if result:
            return result

    # Parse the message as update
    parsed = parse_update_message(message.text)

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
