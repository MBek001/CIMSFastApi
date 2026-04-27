import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_active_user
from database import get_async_session
from models.admin_models import audit_log
from schemes.schemes_audit import AuditLogListResponse, AuditLogResponse
from utils.audit import json_loads_audit

router = APIRouter(prefix="/audit", tags=["Audit Logs"])


def _ensure_audit_access(current_user) -> None:
    if current_user.company_code == "ceo" or current_user.is_admin or current_user.is_superuser:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Audit loglarni ko'rish huquqingiz yo'q")


def _serialize_log(row) -> AuditLogResponse:
    return AuditLogResponse(
        id=row.id,
        created_at=row.created_at.isoformat(),
        actor_user_id=row.actor_user_id,
        actor_email=row.actor_email,
        actor_name=row.actor_name,
        module=row.module,
        table_name=row.table_name,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        action=row.action,
        summary=row.summary,
        before_data=json_loads_audit(row.before_data),
        after_data=json_loads_audit(row.after_data),
        changed_fields=json_loads_audit(row.changed_fields) or [],
        request_id=row.request_id,
        ip_address=row.ip_address,
        user_agent=row.user_agent,
        is_system_action=bool(row.is_system_action),
    )


async def _query_audit_logs(
    session: AsyncSession,
    *,
    module: str | None = None,
    table_name: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    actor_user_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AuditLogListResponse:
    conditions = []
    if module:
        conditions.append(audit_log.c.module == module)
    if table_name:
        conditions.append(audit_log.c.table_name == table_name)
    if entity_type:
        conditions.append(audit_log.c.entity_type == entity_type)
    if entity_id:
        conditions.append(audit_log.c.entity_id == entity_id)
    if action:
        conditions.append(audit_log.c.action == action)
    if actor_user_id is not None:
        conditions.append(audit_log.c.actor_user_id == actor_user_id)

    where_clause = and_(*conditions) if conditions else None
    base_query = select(audit_log)
    count_query = select(func.count(audit_log.c.id))
    if where_clause is not None:
        base_query = base_query.where(where_clause)
        count_query = count_query.where(where_clause)

    total_items = int((await session.execute(count_query)).scalar() or 0)
    offset = (page - 1) * page_size
    rows = (
        await session.execute(
            base_query.order_by(desc(audit_log.c.created_at), desc(audit_log.c.id)).offset(offset).limit(page_size)
        )
    ).fetchall()

    return AuditLogListResponse(
        items=[_serialize_log(row) for row in rows],
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=max(1, math.ceil(total_items / page_size)) if total_items else 1,
    )


@router.get("/logs", response_model=AuditLogListResponse, summary="Audit loglar ro'yxati")
async def list_audit_logs(
    module: str | None = Query(default=None),
    table_name: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    actor_user_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    _ensure_audit_access(current_user)
    return await _query_audit_logs(
        session,
        module=module,
        table_name=table_name,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_user_id=actor_user_id,
        page=page,
        page_size=page_size,
    )


@router.get("/logs/{log_id}", response_model=AuditLogResponse, summary="Bitta audit log")
async def get_audit_log(
    log_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    _ensure_audit_access(current_user)
    result = await session.execute(select(audit_log).where(audit_log.c.id == log_id))
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit log topilmadi")
    return _serialize_log(row)


@router.get("/logs/entity/{entity_type}/{entity_id}", response_model=AuditLogListResponse, summary="Entity bo'yicha audit loglar")
async def get_audit_logs_by_entity(
    entity_type: str,
    entity_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    _ensure_audit_access(current_user)
    return await _query_audit_logs(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        page=page,
        page_size=page_size,
    )


@router.get("/logs/user/{user_id}", response_model=AuditLogListResponse, summary="User bo'yicha audit loglar")
async def get_audit_logs_by_user(
    user_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    _ensure_audit_access(current_user)
    return await _query_audit_logs(
        session,
        actor_user_id=user_id,
        page=page,
        page_size=page_size,
    )
