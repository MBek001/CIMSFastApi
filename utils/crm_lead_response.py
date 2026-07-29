from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin_models import CustomerStatus, audit_log, customer, customer_note, customer_status_change_log


try:
    from zoneinfo import ZoneInfo
    UZBEKISTAN_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    UZBEKISTAN_TZ = timezone(timedelta(hours=5), name="Asia/Tashkent")


LEAD_RESPONSE_LIMIT_MINUTES = 5


async def ensure_status_change_name_columns(session: AsyncSession) -> None:
    await session.execute(text("""
        ALTER TABLE customer_status_change_log
        ADD COLUMN IF NOT EXISTS from_status_name VARCHAR(100)
    """))
    await session.execute(text("""
        ALTER TABLE customer_status_change_log
        ADD COLUMN IF NOT EXISTS to_status_name VARCHAR(100)
    """))
    await session.commit()


def _normalize_dt(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _to_uz_iso(value: Optional[datetime]) -> Optional[str]:
    value = _normalize_dt(value)
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(UZBEKISTAN_TZ).isoformat()


def _local_naive_to_uz_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UZBEKISTAN_TZ)
    else:
        value = value.astimezone(UZBEKISTAN_TZ)
    return value.isoformat()


def _local_naive_to_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UZBEKISTAN_TZ)
    else:
        value = value.astimezone(UZBEKISTAN_TZ)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _minutes_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    start = _normalize_dt(start)
    end = _normalize_dt(end)
    if start is None or end is None:
        return None
    return round(max((end - start).total_seconds(), 0) / 60, 2)


def _empty_to_none(value: Optional[str]) -> Optional[str]:
    value = str(value or "").strip()
    return value or None


def _format_minutes(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if value < 60:
        return f"{value:.1f} minut"
    hours = int(value // 60)
    minutes = int(round(value % 60))
    if hours < 24:
        return f"{hours} soat {minutes} minut"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days} kun {rem_hours} soat {minutes} minut"


async def _first_status_change_rows(session: AsyncSession, customer_ids: list[int]) -> dict[int, dict]:
    if not customer_ids:
        return {}
    await ensure_status_change_name_columns(session)
    first_changed = (
        select(
            customer_status_change_log.c.customer_id.label("customer_id"),
            func.min(customer_status_change_log.c.changed_at).label("changed_at"),
        )
        .where(
            customer_status_change_log.c.customer_id.in_(customer_ids),
            func.coalesce(customer_status_change_log.c.from_status_name, cast(customer_status_change_log.c.from_status, String)) == CustomerStatus.need_to_call.value,
            func.coalesce(customer_status_change_log.c.to_status_name, cast(customer_status_change_log.c.to_status, String)) != CustomerStatus.need_to_call.value,
        )
        .group_by(customer_status_change_log.c.customer_id)
        .subquery()
    )
    result = await session.execute(
        select(
            customer_status_change_log.c.customer_id,
            customer_status_change_log.c.to_status,
            customer_status_change_log.c.to_status_name,
            customer_status_change_log.c.changed_at,
        )
        .select_from(
            customer_status_change_log.join(
                first_changed,
                and_(
                    customer_status_change_log.c.customer_id == first_changed.c.customer_id,
                    customer_status_change_log.c.changed_at == first_changed.c.changed_at,
                ),
            )
        )
    )
    rows = {}
    for row in result.fetchall():
        status_value = row.to_status_name or (row.to_status.value if hasattr(row.to_status, "value") else str(row.to_status or ""))
        rows[int(row.customer_id)] = {
            "status": status_value,
            "changed_at": _normalize_dt(row.changed_at),
        }
    return rows


async def _first_note_rows(session: AsyncSession, customer_ids: list[int]) -> dict[int, datetime]:
    if not customer_ids:
        return {}
    note_result = await session.execute(
        select(
            customer_note.c.customer_id,
            func.min(customer_note.c.created_at).label("note_at"),
        )
        .where(customer_note.c.customer_id.in_(customer_ids))
        .group_by(customer_note.c.customer_id)
    )
    note_rows = {
        int(row.customer_id): _normalize_dt(row.note_at)
        for row in note_result.fetchall()
        if row.note_at is not None
    }
    audit_result = await session.execute(
        select(
            cast(audit_log.c.entity_id, String).label("customer_id"),
            func.min(audit_log.c.created_at).label("note_at"),
        )
        .where(
            audit_log.c.module == "crm",
            audit_log.c.entity_type == "customer",
            audit_log.c.entity_id.in_([str(item) for item in customer_ids]),
            or_(
                audit_log.c.changed_fields.ilike("%notes%"),
                audit_log.c.after_data.ilike('%"notes"%'),
            ),
        )
        .group_by(audit_log.c.entity_id)
    )
    for row in audit_result.fetchall():
        try:
            customer_id = int(row.customer_id)
        except Exception:
            continue
        note_at = _normalize_dt(row.note_at)
        if note_at is not None and (customer_id not in note_rows or note_at < note_rows[customer_id]):
            note_rows[customer_id] = note_at
    return note_rows


def build_lead_response_metric(
    row,
    *,
    first_status: Optional[dict],
    first_note_at: Optional[datetime],
) -> dict:
    created_at = _normalize_dt(row.created_at)
    created_at_for_diff = _local_naive_to_utc_naive(created_at)
    current_status = getattr(row, "status_name", None) or (
        row.status.value if hasattr(row.status, "value") else str(row.status or "")
    )
    first_status_at = first_status["changed_at"] if first_status else None
    first_status_to = first_status["status"] if first_status else None
    main_note = _empty_to_none(getattr(row, "notes", None))
    if main_note and first_note_at is None:
        first_note_at = created_at_for_diff
    response_minutes = _minutes_between(created_at_for_diff, first_status_at)
    note_minutes = _minutes_between(created_at_for_diff, first_note_at)
    late_minutes = max((response_minutes or 0) - LEAD_RESPONSE_LIMIT_MINUTES, 0) if response_minutes is not None else None
    status_changed = first_status_at is not None
    note_written = first_note_at is not None
    is_late = bool(response_minutes is not None and response_minutes > LEAD_RESPONSE_LIMIT_MINUTES)
    status_changed_without_note = bool(status_changed and not note_written)
    if not status_changed:
        message = "Status hali need_to_call dan o'zgarmagan"
    elif status_changed_without_note:
        message = f"Status {response_minutes:.1f} minutda {first_status_to} ga o'zgartirildi, lekin note yozilmadi"
    elif is_late:
        message = f"Kech bog'lanildi: {response_minutes:.1f} minutda status {first_status_to} ga o'zgardi"
    else:
        message = f"O'z vaqtida bog'lanildi: {response_minutes:.1f} minutda status {first_status_to} ga o'zgardi"
    return {
        "lead_created_at": _local_naive_to_uz_iso(created_at),
        "current_status": current_status,
        "first_status_changed_at": _to_uz_iso(first_status_at),
        "first_status_changed_to": first_status_to,
        "response_minutes": response_minutes,
        "response_human": _format_minutes(response_minutes),
        "response_limit_minutes": LEAD_RESPONSE_LIMIT_MINUTES,
        "is_late_response": is_late,
        "late_minutes": round(late_minutes, 2) if late_minutes is not None else None,
        "late_human": _format_minutes(late_minutes),
        "first_note_at": _to_uz_iso(first_note_at),
        "note_minutes": note_minutes,
        "note_human": _format_minutes(note_minutes),
        "note_written": note_written,
        "status_changed": status_changed,
        "status_changed_without_note": status_changed_without_note,
        "message": message,
    }


async def get_customer_lead_response_metric(session: AsyncSession, customer_row) -> dict:
    first_statuses = await _first_status_change_rows(session, [int(customer_row.id)])
    first_notes = await _first_note_rows(session, [int(customer_row.id)])
    return build_lead_response_metric(
        customer_row,
        first_status=first_statuses.get(int(customer_row.id)),
        first_note_at=first_notes.get(int(customer_row.id)),
    )


async def get_lead_response_metrics_for_customers(session: AsyncSession, customer_rows: list) -> dict[int, dict]:
    customer_ids = [int(row.id) for row in customer_rows]
    first_statuses = await _first_status_change_rows(session, customer_ids)
    first_notes = await _first_note_rows(session, customer_ids)
    return {
        int(row.id): build_lead_response_metric(
            row,
            first_status=first_statuses.get(int(row.id)),
            first_note_at=first_notes.get(int(row.id)),
        )
        for row in customer_rows
    }


async def get_lead_response_dashboard_stats(session: AsyncSession) -> dict:
    return await get_lead_response_dashboard_stats_for_period(session)


async def get_lead_response_dashboard_stats_for_period(session: AsyncSession, days: Optional[int] = None) -> dict:
    period_start = None
    period_end = None
    filters = [customer.c.is_archived.is_not(True)]
    if days is not None:
        safe_days = 7 if int(days) == 7 else 3
        period_end = datetime.now(UZBEKISTAN_TZ).replace(tzinfo=None)
        period_start = period_end - timedelta(days=safe_days)
        filters.append(customer.c.created_at >= period_start)
        filters.append(customer.c.created_at < period_end)
        days = safe_days
    result = await session.execute(
        select(customer).where(*filters)
    )
    rows = result.fetchall()
    metrics = await get_lead_response_metrics_for_customers(session, rows)
    eligible = [
        item for item in metrics.values()
        if item["current_status"] == CustomerStatus.need_to_call.value or item["status_changed"]
    ]
    changed = [item for item in eligible if item["status_changed"] and item["response_minutes"] is not None]
    with_note = [item for item in eligible if item["note_written"]]
    changed_with_note = [item for item in changed if item["note_written"]]
    late = [item for item in changed if item["is_late_response"]]
    no_note = [item for item in eligible if not item["note_written"]]
    no_note_after_status = [item for item in eligible if item["status_changed_without_note"]]
    avg_response = round(sum(item["response_minutes"] for item in changed) / len(changed), 2) if changed else None
    avg_response_with_note = round(sum(item["response_minutes"] for item in changed_with_note) / len(changed_with_note), 2) if changed_with_note else None
    avg_note = round(sum(item["note_minutes"] for item in with_note if item["note_minutes"] is not None) / len(with_note), 2) if with_note else None
    avg_late = round(sum(item["late_minutes"] for item in late if item["late_minutes"] is not None) / len(late), 2) if late else None
    status_counts: dict[str, int] = {}
    daily: dict[str, dict] = {}
    for item in changed:
        status_key = item["first_status_changed_to"] or "unknown"
        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        day_key = (item["lead_created_at"] or "")[:10] or "unknown"
        if day_key not in daily:
            daily[day_key] = {
                "date": day_key,
                "total_count": 0,
                "status_changed_count": 0,
                "on_time_count": 0,
                "late_count": 0,
                "note_written_count": 0,
                "note_missing_count": 0,
            }
        daily[day_key]["status_changed_count"] += 1
        if item["is_late_response"]:
            daily[day_key]["late_count"] += 1
        else:
            daily[day_key]["on_time_count"] += 1
    for item in eligible:
        day_key = (item["lead_created_at"] or "")[:10] or "unknown"
        if day_key not in daily:
            daily[day_key] = {
                "date": day_key,
                "total_count": 0,
                "status_changed_count": 0,
                "on_time_count": 0,
                "late_count": 0,
                "note_written_count": 0,
                "note_missing_count": 0,
            }
        daily[day_key]["total_count"] += 1
        if item["note_written"]:
            daily[day_key]["note_written_count"] += 1
        else:
            daily[day_key]["note_missing_count"] += 1
    return {
        "lead_response_limit_minutes": LEAD_RESPONSE_LIMIT_MINUTES,
        "lead_response_days": days,
        "lead_response_period_start": _local_naive_to_uz_iso(period_start),
        "lead_response_period_end": _local_naive_to_uz_iso(period_end),
        "lead_response_total_count": len(eligible),
        "lead_response_status_changed_count": len(changed),
        "lead_response_on_time_count": len([item for item in changed if not item["is_late_response"]]),
        "lead_response_late_count": len(late),
        "lead_response_no_status_change_count": len([item for item in eligible if not item["status_changed"]]),
        "lead_response_note_written_count": len(with_note),
        "lead_response_note_missing_count": len(no_note),
        "lead_response_status_changed_without_note_count": len(no_note_after_status),
        "lead_response_average_minutes": avg_response,
        "lead_response_average_human": _format_minutes(avg_response),
        "lead_response_average_with_note_minutes": avg_response_with_note,
        "lead_response_average_with_note_human": _format_minutes(avg_response_with_note),
        "lead_response_average_note_minutes": avg_note,
        "lead_response_average_note_human": _format_minutes(avg_note),
        "lead_response_average_late_minutes": avg_late,
        "lead_response_average_late_human": _format_minutes(avg_late),
        "lead_response_to_status_counts": status_counts,
        "lead_response_daily": [daily[key] for key in sorted(daily.keys())],
    }
