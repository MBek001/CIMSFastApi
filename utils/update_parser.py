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


def parse_update_message(message_text: str) -> Optional[Dict]:
    """
    Parse Telegram update message and extract username, date, and content

    Args:
        message_text: The full text of the telegram message

    Returns:
        Dict with keys: telegram_username, update_date, update_content
        Returns None if message format is invalid
    """
    if not message_text or len(message_text.strip()) < 10:
        return None

    lines = message_text.strip().split('\n')

    # Find username (starts with #)
    telegram_username = None
    username_line_idx = None

    for idx, line in enumerate(lines):
        line = line.strip()
        if line.startswith('#') and len(line) > 1:
            # Extract username (remove # and any trailing whitespace)
            telegram_username = line[1:].strip().lower()
            username_line_idx = idx
            break

    if not telegram_username or username_line_idx is None:
        return None

    # Try to extract date from first few lines
    update_date = None
    date_patterns = [
        r'update\s+for\s+(\w+\s+\d+)',  # "Update for December 16"
        r'update\s+for\s+(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4})',  # "Update for 16/12/2025"
        r'(\w+\s+\d+)',  # "December 16"
        r'(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4})',  # "16/12/2025"
    ]

    search_text = '\n'.join(lines[:5])  # Search in first 5 lines

    for pattern in date_patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            update_date = parse_date_string(date_str)
            if update_date:
                break

    # If no date found, use today's date
    if not update_date:
        update_date = date.today()

    # Extract update content (everything after username line)
    content_lines = []
    for idx in range(username_line_idx + 1, len(lines)):
        line = lines[idx].strip()
        # Skip empty lines and date lines
        if line and not re.match(r'^(update\s+for|december|january|february|march|april|may|june|july|august|september|october|november|\d{1,2}[./\-])', line, re.IGNORECASE):
            content_lines.append(line)

    if not content_lines:
        return None

    update_content = '\n'.join(content_lines)

    # Validate minimum content length (at least 20 characters)
    if len(update_content) < 20:
        return None

    return {
        'telegram_username': telegram_username,
        'update_date': update_date,
        'update_content': update_content
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
    """
    Validate update content

    Args:
        content: Update content text
        min_length: Minimum required length

    Returns:
        True if valid, False otherwise
    """
    if not content or len(content.strip()) < min_length:
        return False

    # Check if content has at least some meaningful text (not just symbols)
    alphanumeric_count = sum(c.isalnum() for c in content)
    if alphanumeric_count < min_length * 0.7:  # At least 70% alphanumeric
        return False

    return True


def extract_update_stats(content: str) -> Dict[str, int]:
    """
    Extract basic statistics from update content

    Returns:
        Dict with stats like line_count, bullet_count, etc.
    """
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
