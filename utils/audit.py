import json
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from fastapi import Request
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin_models import audit_log


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def json_dumps_audit(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(_json_safe(value), ensure_ascii=False)


def json_loads_audit(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def build_changed_fields(before_data: Optional[dict[str, Any]], after_data: Optional[dict[str, Any]]) -> list[str]:
    before = before_data or {}
    after = after_data or {}
    keys = set(before.keys()) | set(after.keys())
    return sorted(key for key in keys if _json_safe(before.get(key)) != _json_safe(after.get(key)))


def request_metadata(request: Optional[Request]) -> dict[str, Optional[str]]:
    if request is None:
        return {"request_id": None, "ip_address": None, "user_agent": None}
    forwarded_for = request.headers.get("x-forwarded-for")
    ip_address = forwarded_for.split(",")[0].strip() if forwarded_for else None
    if not ip_address and request.client:
        ip_address = request.client.host
    return {
        "request_id": getattr(getattr(request, "state", None), "request_id", None),
        "ip_address": ip_address,
        "user_agent": request.headers.get("user-agent"),
    }


async def log_audit_event(
    session: AsyncSession,
    *,
    module: str,
    table_name: str,
    entity_type: str,
    action: str,
    summary: Optional[str] = None,
    entity_id: Optional[str | int] = None,
    actor_user=None,
    request: Optional[Request] = None,
    before_data: Optional[dict[str, Any]] = None,
    after_data: Optional[dict[str, Any]] = None,
    changed_fields: Optional[list[str]] = None,
    is_system_action: bool = False,
) -> None:
    metadata = request_metadata(request)
    actor_name = None
    actor_email = None
    actor_user_id = None
    if actor_user is not None:
        actor_user_id = getattr(actor_user, "id", None)
        actor_email = getattr(actor_user, "email", None)
        actor_name = " ".join(
            part for part in [getattr(actor_user, "name", None), getattr(actor_user, "surname", None)] if part
        ) or None

    normalized_before = _json_safe(before_data) if before_data is not None else None
    normalized_after = _json_safe(after_data) if after_data is not None else None
    if changed_fields is None:
        changed_fields = build_changed_fields(
            normalized_before if isinstance(normalized_before, dict) else None,
            normalized_after if isinstance(normalized_after, dict) else None,
        )

    await session.execute(
        insert(audit_log).values(
            created_at=datetime.utcnow(),
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            actor_name=actor_name,
            module=module,
            table_name=table_name,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            action=action,
            summary=summary,
            before_data=json_dumps_audit(normalized_before),
            after_data=json_dumps_audit(normalized_after),
            changed_fields=json_dumps_audit(changed_fields),
            request_id=metadata["request_id"],
            ip_address=metadata["ip_address"],
            user_agent=metadata["user_agent"],
            is_system_action=is_system_action,
        )
    )
