from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin_models import workday_override

TARGET_TYPE_ALL = "all"
TARGET_TYPE_MEMBER = "member"
DAY_TYPE_HOLIDAY = "holiday"
DAY_TYPE_SHORT_DAY = "short_day"


def build_target_key(target_type: str, user_id: Optional[int] = None) -> str:
    if target_type == TARGET_TYPE_ALL:
        return TARGET_TYPE_ALL
    if target_type != TARGET_TYPE_MEMBER or user_id is None:
        raise ValueError("Member target uchun user_id majburiy")
    return f"{TARGET_TYPE_MEMBER}:{user_id}"


def normalize_update_required(day_type: str, update_required: Optional[bool]) -> bool:
    if day_type == DAY_TYPE_HOLIDAY:
        return False
    if update_required is not None:
        return bool(update_required)
    return day_type != DAY_TYPE_HOLIDAY


async def fetch_override_pack(
    session: AsyncSession,
    start_date: date,
    end_date: date,
    user_ids: Optional[Sequence[int]] = None,
) -> dict:
    conditions = [
        workday_override.c.special_date >= start_date,
        workday_override.c.special_date <= end_date,
    ]

    normalized_user_ids = sorted({int(user_id) for user_id in user_ids or [] if user_id is not None})
    if normalized_user_ids:
        conditions.append(
            or_(
                workday_override.c.target_type == TARGET_TYPE_ALL,
                workday_override.c.user_id.in_(normalized_user_ids),
            )
        )

    result = await session.execute(
        select(workday_override)
        .where(and_(*conditions))
        .order_by(workday_override.c.special_date.asc(), workday_override.c.id.asc())
    )
    rows = result.fetchall()

    global_overrides = {}
    member_overrides = {}
    for row in rows:
        if row.target_type == TARGET_TYPE_ALL:
            global_overrides[row.special_date] = row
            continue
        if row.user_id is None:
            continue
        member_overrides.setdefault(row.user_id, {})[row.special_date] = row

    return {
        "global": global_overrides,
        "member": member_overrides,
    }


def get_effective_override(override_pack: dict, user_id: int, current_date: date):
    member_map = override_pack.get("member", {}).get(user_id, {})
    if current_date in member_map:
        return member_map[current_date]
    return override_pack.get("global", {}).get(current_date)


def is_expected_update_day(override_pack: dict, user_id: int, current_date: date) -> bool:
    if current_date.weekday() == 6:
        return False

    effective = get_effective_override(override_pack, user_id, current_date)
    if effective is None:
        return True

    if getattr(effective, "day_type", None) == DAY_TYPE_HOLIDAY:
        return False

    return bool(getattr(effective, "update_required", True))


def list_expected_update_days(
    override_pack: dict,
    user_id: int,
    start_date: date,
    end_date: date,
) -> list[date]:
    expected_days: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        if is_expected_update_day(override_pack, user_id, cursor):
            expected_days.append(cursor)
        cursor += timedelta(days=1)
    return expected_days


def summarize_expected_days(
    override_pack: dict,
    user_id: int,
    start_date: date,
    end_date: date,
) -> dict:
    cursor = start_date
    sundays_count = 0
    day_off_count = 0
    short_day_count = 0
    working_days = 0

    while cursor <= end_date:
        if cursor.weekday() == 6:
            sundays_count += 1
            cursor += timedelta(days=1)
            continue

        effective = get_effective_override(override_pack, user_id, cursor)
        if effective is not None and effective.day_type == DAY_TYPE_SHORT_DAY:
            short_day_count += 1

        if is_expected_update_day(override_pack, user_id, cursor):
            working_days += 1
        else:
            day_off_count += 1

        cursor += timedelta(days=1)

    return {
        "working_days": working_days,
        "sundays_count": sundays_count,
        "day_off_count": day_off_count,
        "short_day_count": short_day_count,
    }
