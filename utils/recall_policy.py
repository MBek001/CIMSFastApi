from datetime import datetime, timezone
from typing import Any, Optional

from config import GOOGLE_CALENDAR_EVENT_DURATION_MINUTES

CONTINUING_STATUS_KEY = "continuing"
CONTINUING_EVENT_DURATION_MINUTES = 60
CONTINUING_REMINDER_MINUTES = 60
DEFAULT_REMINDER_MINUTES = 5


def normalize_status_key(status: Any) -> Optional[str]:
    if status is None:
        return None

    if hasattr(status, "value"):
        value = getattr(status, "value")
    else:
        value = status

    if value is None:
        return None
    return str(value).strip().lower() or None


def is_continuing_status(status: Any) -> bool:
    return normalize_status_key(status) == CONTINUING_STATUS_KEY


def get_event_duration_minutes(status: Any) -> int:
    if is_continuing_status(status):
        return CONTINUING_EVENT_DURATION_MINUTES
    return GOOGLE_CALENDAR_EVENT_DURATION_MINUTES


def get_target_reminder_minutes(status: Any) -> int:
    if is_continuing_status(status):
        return CONTINUING_REMINDER_MINUTES
    return DEFAULT_REMINDER_MINUTES


def get_effective_reminder_minutes(
    *,
    status: Any,
    recall_time: datetime,
    now: Optional[datetime] = None,
) -> int:
    reminder_minutes = get_target_reminder_minutes(status)
    current_time = now or datetime.now(timezone.utc)

    if recall_time.tzinfo is None:
        recall_time = recall_time.replace(tzinfo=timezone.utc)
    else:
        recall_time = recall_time.astimezone(timezone.utc)

    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    if recall_time <= current_time:
        return 0

    reminder_delta_seconds = reminder_minutes * 60
    time_until_event_seconds = (recall_time - current_time).total_seconds()
    if time_until_event_seconds < reminder_delta_seconds:
        return 0

    return reminder_minutes
