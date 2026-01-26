"""
Update Parser Utility
Parses daily update messages from Telegram channel
Expected format:
    Update for December 16
    #username
    - task 1
    - task 2
    - task 3
"""
import re
from datetime import datetime, date
from typing import Optional, Dict, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.user_models import user


def parse_update_message(message_text: str):
    if not message_text or len(message_text.strip()) < 10:
        return None

    lines = message_text.strip().split('\n')

    # 1. Username
    telegram_username = None
    username_line_idx = None

    for idx, line in enumerate(lines):
        line = line.strip()
        if line.startswith('#') and len(line) > 1:
            telegram_username = line[1:].strip().lower()
            username_line_idx = idx
            break

    if not telegram_username:
        return None

    # 2. Date
    update_date = None
    search_text = '\n'.join(lines[:5])

    date_patterns = [
        r'update\s+for\s+(\w+\s+\d+)',
        r'(\w+\s+\d+)',
    ]

    for pattern in date_patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            update_date = parse_date_string(match.group(1))
            break

    if not update_date:
        update_date = date.today()

    # 3. Content (hamma narsani username dan keyin olamiz)
    content_lines = []

    for line in lines[username_line_idx + 1:]:
        line = line.strip()
        if not line:
            continue

        # Normalize bullets:
        # 1. task -> - task
        if re.match(r'^\d+\.\s+', line):
            line = "- " + re.sub(r'^\d+\.\s+', '', line)

        content_lines.append(line)

    if not content_lines:
        return None

    update_content = "\n".join(content_lines)

    return {
        "telegram_username": telegram_username,
        "update_date": update_date,
        "update_content": update_content
    }



def parse_date_string(date_str: str) -> Optional[date]:
    """
    Parse various date formats into date object

    Supported formats:
    - "December 16" (assumes current year)
    - "16/12/2025", "16-12-2025", "16.12.2025"
    - "12/16/2025" (US format)
    """
    date_str = date_str.strip()
    current_year = datetime.now().year

    # Try month name format: "December 16"
    month_names = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12
    }

    # Check for "Month Day" format
    match = re.match(r'(\w+)\s+(\d+)', date_str, re.IGNORECASE)
    if match:
        month_str = match.group(1).lower()
        day = int(match.group(2))

        if month_str in month_names:
            month = month_names[month_str]
            try:
                return date(current_year, month, day)
            except ValueError:
                return None

    # Try numeric formats: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY
    separators = ['/', '-', '.']
    for sep in separators:
        if sep in date_str:
            parts = date_str.split(sep)
            if len(parts) == 3:
                try:
                    # Try DD/MM/YYYY
                    day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                    if year < 100:  # 2-digit year
                        year += 2000
                    return date(year, month, day)
                except (ValueError, IndexError):
                    try:
                        # Try MM/DD/YYYY (US format)
                        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
                        if year < 100:
                            year += 2000
                        return date(year, month, day)
                    except (ValueError, IndexError):
                        pass

    return None


async def find_user_by_telegram_username(
    session: AsyncSession,
    telegram_username: str
) -> Optional[int]:
    """
    Find user ID by telegram username
    Matches against user.telegram_id or extracts from user.email

    Args:
        session: Database session
        telegram_username: Telegram username (without @)

    Returns:
        User ID if found, None otherwise
    """
    telegram_username = telegram_username.lower().strip()

    # Try exact match on telegram_id
    result = await session.execute(
        select(user.c.id)
        .where(user.c.telegram_id == telegram_username)
    )
    user_row = result.fetchone()
    if user_row:
        return user_row.id

    # Try fuzzy match on name/surname
    result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.email)
        .where(user.c.is_active == True)
    )
    users = result.fetchall()

    for u in users:
        # Check if username matches name or surname (case-insensitive)
        if (telegram_username in u.name.lower() or
            telegram_username in u.surname.lower() or
            telegram_username == f"{u.name.lower()}{u.surname.lower()}" or
            telegram_username == f"{u.surname.lower()}{u.name.lower()}"):
            return u.id

        # Check email prefix (before @)
        if u.email:
            email_prefix = u.email.split('@')[0].lower()
            if telegram_username == email_prefix:
                return u.id

    return None


def validate_update_content(content: str, min_length: int = 20) -> bool:
    if not content or len(content.strip()) < min_length:
        return False

    lines = [l.strip() for l in content.split('\n') if l.strip()]

    # Kamida 2 ta meaningful line bo‘lsin
    if len(lines) < 2:
        return False

    # Har bir line kamida 3 ta so‘zdan iborat bo‘lsin
    meaningful = [l for l in lines if len(l.split()) >= 3]

    return len(meaningful) >= 2



def extract_update_stats(content: str) -> Dict[str, int]:

    lines = [line.strip() for line in content.split('\n') if line.strip()]

    # Count bullet points (lines starting with -, •, *, number.)
    bullet_count = 0
    for line in lines:
        if re.match(r'^[\-\•\*]\s+', line) or re.match(r'^\d+[\.\)]\s+', line):
            bullet_count += 1

    return {
        'line_count': len(lines),
        'bullet_count': bullet_count,
        'character_count': len(content),
        'word_count': len(content.split())
    }
