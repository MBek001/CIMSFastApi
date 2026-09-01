import calendar
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_active_user
from database import engine, get_async_session, async_session_maker
from models.developer_kpi_models import (
    developer_kpi_blocked_period,
    developer_kpi_deduction,
    developer_kpi_feature,
    developer_kpi_quality_event,
    developer_kpi_salary_snapshot,
    developer_work_schedule,
)
from models.projects_models import project, project_board, project_board_card, project_board_card_assignee, project_board_card_status_history, project_board_column
from models.user_models import UserRole, attendance_daily_record, user
from schemes.developer_kpi_schemes import (
    DeveloperKpiBlockedPeriodRequest,
    DeveloperKpiBlockedPeriodUpdateRequest,
    DeveloperKpiDeductionUpdateRequest,
    DeveloperKpiFeatureAcceptRequest,
    DeveloperKpiFeatureCreateRequest,
    DeveloperKpiFeatureUpdateRequest,
    DeveloperKpiQualityEventRequest,
    DeveloperKpiQualityEventUpdateRequest,
    DeveloperProjectDeliveryRequest,
    DeveloperWorkScheduleRequest,
)

router = APIRouter(prefix="/developer-kpi", tags=["Developer KPI"])
_developer_kpi_schema_ready = False

DELIVERY_WEIGHT = Decimal("0.35")
DEADLINE_WEIGHT = Decimal("0.20")
QUALITY_WEIGHT = Decimal("0.20")
TEAM_WEIGHT = Decimal("0.15")
DISCIPLINE_WEIGHT = Decimal("0.10")
MAX_KPI_FUND_PERCENT = Decimal("15")
DEFAULT_WORK_START = time(11, 0)
DEFAULT_WORK_END = time(21, 0)
DEFAULT_FREE_START = time(13, 0)
DEFAULT_FREE_END = time(15, 0)
WORKFLOW_DISCIPLINE_SCORE = Decimal("100")
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")


async def ensure_developer_kpi_schema() -> None:
    global _developer_kpi_schema_ready
    if _developer_kpi_schema_ready:
        return
    async with engine.begin() as conn:
        await conn.execute(text("""ALTER TABLE project ADD COLUMN IF NOT EXISTS actual_delivery_date DATE NULL"""))
        await conn.execute(text("""ALTER TABLE project ADD COLUMN IF NOT EXISTS delivery_status VARCHAR(50) NULL"""))
        await conn.execute(text("""ALTER TABLE project ADD COLUMN IF NOT EXISTS approved_blocked_days INTEGER NOT NULL DEFAULT 0"""))
        await conn.execute(text("""ALTER TABLE project ADD COLUMN IF NOT EXISTS real_delay_days INTEGER NULL"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_actual_delivery_date ON project(actual_delivery_date)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_delivery_status ON project(delivery_status)"""))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS developer_work_schedule (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                weekday INTEGER NOT NULL,
                work_start_time TIME NOT NULL,
                work_end_time TIME NOT NULL,
                free_start_time TIME NULL,
                free_end_time TIME NULL,
                late_grace_minutes INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_developer_work_schedule_user_weekday UNIQUE (user_id, weekday)
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS developer_kpi_feature (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                description TEXT NULL,
                acceptance_criteria TEXT NULL,
                points INTEGER NOT NULL,
                owner_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE SET NULL,
                frontend_percent INTEGER NOT NULL DEFAULT 0,
                backend_percent INTEGER NOT NULL DEFAULT 100,
                due_date DATE NOT NULL,
                status VARCHAR(40) NOT NULL DEFAULT 'planned',
                is_mandatory BOOLEAN NOT NULL DEFAULT TRUE,
                is_locked BOOLEAN NOT NULL DEFAULT FALSE,
                locked_at TIMESTAMP NULL,
                locked_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                accepted_at TIMESTAMP NULL,
                accepted_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                rejected_at TIMESTAMP NULL,
                rejected_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                created_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS developer_kpi_blocked_period (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                feature_id INTEGER NULL REFERENCES developer_kpi_feature(id) ON DELETE CASCADE,
                employee_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                started_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP NULL,
                reason TEXT NOT NULL,
                dependency TEXT NULL,
                evidence_url TEXT NULL,
                is_external BOOLEAN NOT NULL DEFAULT TRUE,
                approval_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                approved_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                approved_at TIMESTAMP NULL,
                created_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS developer_kpi_quality_event (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                feature_id INTEGER NULL REFERENCES developer_kpi_feature(id) ON DELETE SET NULL,
                card_id INTEGER NULL REFERENCES project_board_card(id) ON DELETE SET NULL,
                employee_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                severity VARCHAR(40) NOT NULL,
                source VARCHAR(40) NOT NULL DEFAULT 'manual',
                title VARCHAR(255) NOT NULL,
                description TEXT NULL,
                event_date DATE NOT NULL,
                confirmed BOOLEAN NOT NULL DEFAULT TRUE,
                is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
                external_cause BOOLEAN NOT NULL DEFAULT FALSE,
                created_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS developer_kpi_deduction (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                project_id INTEGER NULL REFERENCES project(id) ON DELETE CASCADE,
                deduction_type VARCHAR(60) NOT NULL,
                percent NUMERIC(5,2) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'candidate',
                trigger_source VARCHAR(120) NOT NULL,
                reason TEXT NOT NULL,
                period_year INTEGER NOT NULL,
                period_month INTEGER NOT NULL,
                approved_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                approved_at TIMESTAMP NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_developer_kpi_deduction_once UNIQUE (employee_id, project_id, deduction_type, period_year, period_month)
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS developer_kpi_salary_snapshot (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                period_year INTEGER NOT NULL,
                period_month INTEGER NOT NULL,
                base_salary NUMERIC(12,2) NOT NULL,
                max_kpi_fund NUMERIC(12,2) NOT NULL,
                delivery_score NUMERIC(6,2) NOT NULL,
                deadline_score NUMERIC(6,2) NOT NULL,
                quality_score NUMERIC(6,2) NOT NULL,
                team_score NUMERIC(6,2) NOT NULL,
                discipline_score NUMERIC(6,2) NOT NULL,
                final_kpi NUMERIC(6,2) NOT NULL,
                kpi_bonus NUMERIC(12,2) NOT NULL,
                approved_deductions NUMERIC(12,2) NOT NULL,
                expected_salary NUMERIC(12,2) NOT NULL,
                source_payload TEXT NOT NULL,
                frozen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                created_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                CONSTRAINT uq_developer_kpi_salary_snapshot UNIQUE (employee_id, period_year, period_month)
            )
        """))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_developer_work_schedule_user ON developer_work_schedule(user_id)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_developer_kpi_feature_owner_due ON developer_kpi_feature(owner_id, due_date)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_developer_kpi_feature_project ON developer_kpi_feature(project_id)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_developer_kpi_blocked_employee ON developer_kpi_blocked_period(employee_id, started_at)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_developer_kpi_quality_employee ON developer_kpi_quality_event(employee_id, event_date)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_developer_kpi_deduction_employee_period ON developer_kpi_deduction(employee_id, period_year, period_month)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_developer_kpi_snapshot_period ON developer_kpi_salary_snapshot(period_year, period_month)"""))
    _developer_kpi_schema_ready = True


@router.on_event("startup")
async def warm_developer_kpi_schema() -> None:
    await ensure_developer_kpi_schema()


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def score(value) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), Decimal(str(value or 0)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def score_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_ceo(current_user) -> bool:
    role = getattr(current_user, "role", None)
    role_name = str(getattr(role, "name", "") or "").lower()
    role_value = str(getattr(role, "value", "") or "").lower()
    role_plain = str(role or "").lower()
    company_code = str(getattr(current_user, "company_code", "") or "").lower()
    return "ceo" in {role_name, role_value, role_plain, company_code}


def is_dev_team_leader(current_user) -> bool:
    values = [
        getattr(current_user, "role_name", None),
        getattr(current_user, "job_title", None),
        getattr(current_user, "company_code", None),
    ]
    joined = " ".join(str(value or "").lower().replace("_", " ") for value in values)
    return "dev team leader" in joined or "developer team leader" in joined or "team leader" in joined


async def ensure_kpi_manager(current_user) -> None:
    if is_ceo(current_user) or is_dev_team_leader(current_user):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Faqat CEO yoki Dev Team Leader")


async def ensure_member(session: AsyncSession, user_id: int):
    row = (await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.default_salary, user.c.role)
        .where(and_(user.c.id == user_id, user.c.is_active == True))
    )).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User topilmadi")
    return row


async def ensure_project(session: AsyncSession, project_id: int):
    row = (await session.execute(select(project).where(project.c.id == project_id))).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project topilmadi")
    return row


def month_range(year: int, month: int) -> Tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def business_delay_days(due_date: date, accepted_at: Optional[datetime], blocked_days: int = 0) -> int:
    if not accepted_at:
        accepted_day = datetime.utcnow().date()
    else:
        accepted_day = accepted_at.date()
    if accepted_day <= due_date:
        return 0
    current = due_date + timedelta(days=1)
    days = 0
    while current <= accepted_day:
        if current.weekday() != 6:
            days += 1
        current += timedelta(days=1)
    return max(0, days - blocked_days)


def deadline_score_for_delay(delay_days: int) -> Decimal:
    if delay_days <= 0:
        return Decimal("100")
    if delay_days == 1:
        return Decimal("90")
    if delay_days == 2:
        return Decimal("80")
    if delay_days == 3:
        return Decimal("65")
    if delay_days <= 5:
        return Decimal("40")
    return Decimal("0")


def date_json(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def feature_payload(row) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "project_name": getattr(row, "project_name", None),
        "title": row.title,
        "description": row.description,
        "acceptance_criteria": row.acceptance_criteria,
        "points": row.points,
        "owner_id": row.owner_id,
        "owner_full_name": f"{getattr(row, 'owner_name', '') or ''} {getattr(row, 'owner_surname', '') or ''}".strip() or None,
        "frontend_percent": row.frontend_percent,
        "backend_percent": row.backend_percent,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "status": row.status,
        "is_mandatory": row.is_mandatory,
        "is_locked": row.is_locked,
        "locked_at": row.locked_at.isoformat() if row.locked_at else None,
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
        "rejected_at": row.rejected_at.isoformat() if row.rejected_at else None,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def approved_blocked_days_for_feature(session: AsyncSession, feature_id: int, employee_id: int) -> int:
    rows = (await session.execute(
        select(developer_kpi_blocked_period.c.started_at, developer_kpi_blocked_period.c.ended_at)
        .where(and_(
            developer_kpi_blocked_period.c.feature_id == feature_id,
            developer_kpi_blocked_period.c.employee_id == employee_id,
            developer_kpi_blocked_period.c.approval_status == "approved",
            developer_kpi_blocked_period.c.is_external == True,
            developer_kpi_blocked_period.c.ended_at.is_not(None),
        ))
    )).fetchall()
    days = 0
    for row in rows:
        start_day = row.started_at.date()
        end_day = row.ended_at.date()
        current = start_day
        while current <= end_day:
            if current.weekday() != 6:
                days += 1
            current += timedelta(days=1)
    return days


async def calculate_delivery_deadline(session: AsyncSession, employee_id: int, year: int, month: int) -> Tuple[dict, Decimal, Decimal]:
    start_day, end_day = month_range(year, month)
    rows = (await session.execute(
        select(developer_kpi_feature)
        .where(and_(
            developer_kpi_feature.c.owner_id == employee_id,
            developer_kpi_feature.c.due_date >= start_day,
            developer_kpi_feature.c.due_date <= end_day,
            developer_kpi_feature.c.is_mandatory == True,
            developer_kpi_feature.c.is_locked == True,
        ))
        .order_by(developer_kpi_feature.c.due_date.asc(), developer_kpi_feature.c.id.asc())
    )).fetchall()
    planned_points = sum(int(row.points or 0) for row in rows)
    accepted_rows = [row for row in rows if row.accepted_at and start_day <= row.accepted_at.date() <= end_day]
    accepted_points = sum(int(row.points or 0) for row in accepted_rows)
    delivery = score(Decimal(accepted_points) * Decimal("100") / Decimal(planned_points)) if planned_points else Decimal("100")
    weighted_deadline = Decimal("0")
    deadline_weight_points = 0
    feature_items = []
    for row in rows:
        blocked_days = await approved_blocked_days_for_feature(session, row.id, employee_id)
        delay_days = business_delay_days(row.due_date, row.accepted_at, blocked_days)
        row_deadline_score = deadline_score_for_delay(delay_days) if row.accepted_at else Decimal("0")
        weighted_deadline += row_deadline_score * Decimal(row.points or 0)
        deadline_weight_points += int(row.points or 0)
        feature_items.append({
            "id": row.id,
            "title": row.title,
            "points": row.points,
            "due_date": row.due_date.isoformat(),
            "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
            "delay_business_days": delay_days,
            "approved_blocked_days": blocked_days,
            "deadline_score": score_float(row_deadline_score),
            "status": row.status,
        })
    deadline = score(weighted_deadline / Decimal(deadline_weight_points)) if deadline_weight_points else Decimal("100")
    return {
        "planned_points": planned_points,
        "accepted_points": accepted_points,
        "features": feature_items,
    }, delivery, deadline


def quality_penalty_by_severity(severity: str) -> Decimal:
    normalized = str(severity or "").strip().lower()
    if normalized in {"minor_qa_reopen", "qa_minor", "minor", "functional"}:
        return Decimal("2")
    if normalized in {"major_qa_reopen", "qa_major", "major", "prod_bug"}:
        return Decimal("5")
    if normalized in {"major_prod_bug", "prod_major"}:
        return Decimal("10")
    if normalized in {"critical_prod_incident", "critical", "prod_critical"}:
        return Decimal("20")
    return Decimal("2")


async def calculate_quality(session: AsyncSession, employee_id: int, year: int, month: int) -> Tuple[dict, Decimal]:
    start_day, end_day = month_range(year, month)
    manual_rows = (await session.execute(
        select(developer_kpi_quality_event)
        .where(and_(
            developer_kpi_quality_event.c.employee_id == employee_id,
            developer_kpi_quality_event.c.event_date >= start_day,
            developer_kpi_quality_event.c.event_date <= end_day,
            developer_kpi_quality_event.c.confirmed == True,
            developer_kpi_quality_event.c.is_duplicate == False,
            developer_kpi_quality_event.c.external_cause == False,
        ))
    )).fetchall()
    auto_rows = (await session.execute(
        select(
            project_board_card_status_history.c.card_id,
            project_board_card_status_history.c.column_name,
            project_board_card_status_history.c.entered_at,
            project_board_card.c.title,
            project.c.project_name,
        )
        .select_from(
            project_board_card_status_history
            .join(project_board_card, project_board_card_status_history.c.card_id == project_board_card.c.id)
            .join(project_board_column, project_board_card.c.column_id == project_board_column.c.id)
            .join(project_board, project_board_column.c.board_id == project_board.c.id)
            .join(project, project_board.c.project_id == project.c.id)
        )
        .where(and_(
            or_(
                project_board_card.c.assignee_id == employee_id,
                project_board_card.c.id.in_(select(project_board_card_assignee.c.card_id).where(project_board_card_assignee.c.user_id == employee_id)),
            ),
            project_board_card_status_history.c.entered_at >= datetime.combine(start_day, time.min),
            project_board_card_status_history.c.entered_at <= datetime.combine(end_day, time.max),
            or_(
                func.lower(project_board_card_status_history.c.column_name).like("%refix%"),
                func.lower(project_board_card_status_history.c.column_name).like("%reopen%"),
            ),
        ))
    )).fetchall()
    manual_penalty = sum((quality_penalty_by_severity(row.severity) for row in manual_rows), Decimal("0"))
    auto_penalty = Decimal(len(auto_rows) * 2)
    total_penalty = manual_penalty + auto_penalty
    quality = score(Decimal("100") - total_penalty)
    return {
        "manual_events": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "feature_id": row.feature_id,
                "card_id": row.card_id,
                "severity": row.severity,
                "title": row.title,
                "event_date": row.event_date.isoformat(),
                "penalty": score_float(quality_penalty_by_severity(row.severity)),
            }
            for row in manual_rows
        ],
        "auto_refix_reopen_events": [
            {
                "card_id": row.card_id,
                "title": row.title,
                "project_name": row.project_name,
                "column_name": row.column_name,
                "entered_at": row.entered_at.isoformat() if row.entered_at else None,
                "penalty": 2,
            }
            for row in auto_rows
        ],
        "manual_penalty": score_float(manual_penalty),
        "auto_penalty": score_float(auto_penalty),
        "total_penalty": score_float(total_penalty),
    }, quality


async def get_schedule_map(session: AsyncSession, employee_id: int) -> Dict[int, dict]:
    rows = (await session.execute(
        select(developer_work_schedule).where(and_(
            developer_work_schedule.c.user_id == employee_id,
            developer_work_schedule.c.is_active == True,
        ))
    )).fetchall()
    return {
        row.weekday: {
            "work_start_time": row.work_start_time,
            "work_end_time": row.work_end_time,
            "free_start_time": row.free_start_time,
            "free_end_time": row.free_end_time,
            "late_grace_minutes": int(row.late_grace_minutes or 0),
        }
        for row in rows
    }


def default_schedule() -> dict:
    return {
        "work_start_time": DEFAULT_WORK_START,
        "work_end_time": DEFAULT_WORK_END,
        "free_start_time": DEFAULT_FREE_START,
        "free_end_time": DEFAULT_FREE_END,
        "late_grace_minutes": 0,
    }


async def calculate_discipline(session: AsyncSession, employee_id: int, year: int, month: int) -> Tuple[dict, Decimal]:
    start_day, end_day = month_range(year, month)
    today = datetime.utcnow().date()
    if end_day > today:
        end_day = today
    schedule_map = await get_schedule_map(session, employee_id)
    rows = (await session.execute(
        select(attendance_daily_record)
        .where(and_(
            attendance_daily_record.c.employee_id == employee_id,
            attendance_daily_record.c.attendance_date >= start_day,
            attendance_daily_record.c.attendance_date <= end_day,
            attendance_daily_record.c.is_deleted == False,
        ))
    )).fetchall()
    attendance_by_date = {row.attendance_date: row for row in rows}
    expected_days = 0
    late_days = 0
    absent_days = 0
    incomplete_days = 0
    items = []
    current = start_day
    while current <= end_day:
        if current.weekday() == 6:
            current += timedelta(days=1)
            continue
        schedule = schedule_map.get(current.weekday()) or default_schedule()
        expected_days += 1
        record = attendance_by_date.get(current)
        status_value = getattr(record, "status", None) if record else "absent"
        check_in = getattr(record, "check_in_time", None) if record else None
        is_absent = record is None or str(status_value or "").lower() == "absent"
        is_incomplete = bool(record and str(status_value or "").lower() == "incomplete")
        grace_limit = (datetime.combine(current, schedule["work_start_time"]) + timedelta(minutes=schedule["late_grace_minutes"])).time()
        is_late = bool(check_in and check_in > grace_limit)
        if is_absent:
            absent_days += 1
        elif is_incomplete:
            incomplete_days += 1
        elif is_late:
            late_days += 1
        items.append({
            "date": current.isoformat(),
            "status": status_value,
            "check_in_time": check_in.isoformat() if check_in else None,
            "work_start_time": schedule["work_start_time"].isoformat(),
            "late_grace_minutes": schedule["late_grace_minutes"],
            "is_late": is_late,
            "is_absent": is_absent,
            "is_incomplete": is_incomplete,
        })
        current += timedelta(days=1)
    attendance_score = score(Decimal("100") - Decimal(late_days * 5) - Decimal(absent_days * 10) - Decimal(incomplete_days * 3))
    discipline = score((attendance_score * Decimal("0.70")) + (WORKFLOW_DISCIPLINE_SCORE * Decimal("0.30")))
    return {
        "expected_days": expected_days,
        "late_days": late_days,
        "absent_days": absent_days,
        "incomplete_days": incomplete_days,
        "attendance_score": score_float(attendance_score),
        "workflow_score": score_float(WORKFLOW_DISCIPLINE_SCORE),
        "items": items,
    }, discipline


async def calculate_team_score(session: AsyncSession, year: int, month: int) -> Tuple[dict, Decimal]:
    employee_rows = (await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.default_salary)
        .where(and_(user.c.is_active == True, user.c.role == UserRole.member))
    )).fetchall()
    if not employee_rows:
        return {"employees_count": 0}, Decimal("100")
    delivery_scores = []
    deadline_scores = []
    quality_scores = []
    for row in employee_rows:
        _, delivery, deadline = await calculate_delivery_deadline(session, row.id, year, month)
        _, quality = await calculate_quality(session, row.id, year, month)
        delivery_scores.append(delivery)
        deadline_scores.append(deadline)
        quality_scores.append(quality)
    delivery_avg = sum(delivery_scores, Decimal("0")) / Decimal(len(delivery_scores))
    deadline_avg = sum(deadline_scores, Decimal("0")) / Decimal(len(deadline_scores))
    quality_avg = sum(quality_scores, Decimal("0")) / Decimal(len(quality_scores))
    team_score = score(delivery_avg * Decimal("0.60") + deadline_avg * Decimal("0.25") + quality_avg * Decimal("0.15"))
    return {
        "employees_count": len(employee_rows),
        "team_delivery_score": score_float(delivery_avg),
        "team_deadline_score": score_float(deadline_avg),
        "team_quality_score": score_float(quality_avg),
    }, team_score


async def create_auto_deduction_events(session: AsyncSession, employee_id: int, year: int, month: int) -> None:
    start_day, end_day = month_range(year, month)
    delivery_rows = (await session.execute(
        select(project.c.id, project.c.deadline, project.c.actual_delivery_date, project.c.approved_blocked_days)
        .select_from(project.join(developer_kpi_feature, developer_kpi_feature.c.project_id == project.c.id))
        .where(and_(
            developer_kpi_feature.c.owner_id == employee_id,
            developer_kpi_feature.c.is_locked == True,
            project.c.deadline.is_not(None),
            project.c.actual_delivery_date.is_not(None),
            project.c.actual_delivery_date >= start_day,
            project.c.actual_delivery_date <= end_day,
        ))
        .group_by(project.c.id, project.c.deadline, project.c.actual_delivery_date, project.c.approved_blocked_days)
    )).fetchall()
    now = datetime.utcnow()
    for row in delivery_rows:
        planned_day = row.deadline.date() if isinstance(row.deadline, datetime) else row.deadline
        delay_days = business_delay_days(planned_day, datetime.combine(row.actual_delivery_date, time.min), int(row.approved_blocked_days or 0))
        if delay_days > 3:
            await session.execute(
                update(project)
                .where(project.c.id == row.id)
                .values(real_delay_days=delay_days, updated_at=now)
            )
            await session.execute(
                pg_insert(developer_kpi_deduction)
                .values(
                    employee_id=employee_id,
                    project_id=row.id,
                    deduction_type="PROJECT_DEADLINE_DEDUCTION",
                    percent=Decimal("10.00"),
                    status="candidate",
                    trigger_source="project_delivery_delay",
                    reason=f"Project delivery kechikdi: {delay_days} ish kuni",
                    period_year=year,
                    period_month=month,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        developer_kpi_deduction.c.employee_id,
                        developer_kpi_deduction.c.project_id,
                        developer_kpi_deduction.c.deduction_type,
                        developer_kpi_deduction.c.period_year,
                        developer_kpi_deduction.c.period_month,
                    ]
                )
            )
    rows = (await session.execute(
        select(developer_kpi_quality_event.c.project_id, developer_kpi_quality_event.c.severity, func.count().label("count"))
        .where(and_(
            developer_kpi_quality_event.c.employee_id == employee_id,
            developer_kpi_quality_event.c.event_date >= start_day,
            developer_kpi_quality_event.c.event_date <= end_day,
            developer_kpi_quality_event.c.confirmed == True,
            developer_kpi_quality_event.c.is_duplicate == False,
            developer_kpi_quality_event.c.external_cause == False,
        ))
        .group_by(developer_kpi_quality_event.c.project_id, developer_kpi_quality_event.c.severity)
    )).fetchall()
    project_counts: Dict[int, Dict[str, int]] = {}
    for row in rows:
        bucket = project_counts.setdefault(row.project_id, {"functional": 0, "major": 0, "critical": 0})
        normalized = str(row.severity or "").lower()
        if "critical" in normalized:
            bucket["critical"] += int(row.count)
        elif "major" in normalized or "prod" in normalized:
            bucket["major"] += int(row.count)
        else:
            bucket["functional"] += int(row.count)
    for project_id, counts in project_counts.items():
        if counts["functional"] >= 5 or counts["major"] >= 2 or counts["critical"] >= 1:
            await session.execute(
                pg_insert(developer_kpi_deduction)
                .values(
                    employee_id=employee_id,
                    project_id=project_id,
                    deduction_type="PROJECT_QUALITY_DEDUCTION",
                    percent=Decimal("10.00"),
                    status="candidate",
                    trigger_source="quality_threshold",
                    reason=f"Quality threshold: functional={counts['functional']}, major={counts['major']}, critical={counts['critical']}",
                    period_year=year,
                    period_month=month,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        developer_kpi_deduction.c.employee_id,
                        developer_kpi_deduction.c.project_id,
                        developer_kpi_deduction.c.deduction_type,
                        developer_kpi_deduction.c.period_year,
                        developer_kpi_deduction.c.period_month,
                    ]
                )
            )


async def approved_deduction_amount(session: AsyncSession, employee_id: int, year: int, month: int, base_salary: Decimal) -> Tuple[Decimal, List[dict]]:
    rows = (await session.execute(
        select(developer_kpi_deduction)
        .where(and_(
            developer_kpi_deduction.c.employee_id == employee_id,
            developer_kpi_deduction.c.period_year == year,
            developer_kpi_deduction.c.period_month == month,
        ))
        .order_by(developer_kpi_deduction.c.id.desc())
    )).fetchall()
    items = []
    total = Decimal("0")
    for row in rows:
        amount = money(base_salary * Decimal(str(row.percent or 0)) / Decimal("100"))
        if row.status == "approved":
            total += amount
        items.append({
            "id": row.id,
            "project_id": row.project_id,
            "deduction_type": row.deduction_type,
            "percent": float(row.percent or 0),
            "amount": float(amount),
            "status": row.status,
            "reason": row.reason,
            "trigger_source": row.trigger_source,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return money(total), items


async def build_salary_estimate(session: AsyncSession, employee_id: int, year: int, month: int, persist_candidates: bool = True) -> dict:
    employee = await ensure_member(session, employee_id)
    if persist_candidates:
        await create_auto_deduction_events(session, employee_id, year, month)
        await session.commit()
    delivery_payload, delivery, deadline = await calculate_delivery_deadline(session, employee_id, year, month)
    quality_payload, quality = await calculate_quality(session, employee_id, year, month)
    discipline_payload, discipline = await calculate_discipline(session, employee_id, year, month)
    team_payload, team = await calculate_team_score(session, year, month)
    final_kpi = score(delivery * DELIVERY_WEIGHT + deadline * DEADLINE_WEIGHT + quality * QUALITY_WEIGHT + team * TEAM_WEIGHT + discipline * DISCIPLINE_WEIGHT)
    base_salary = money(employee.default_salary)
    max_kpi_fund = money(base_salary * MAX_KPI_FUND_PERCENT / Decimal("100"))
    kpi_bonus = money(max_kpi_fund * final_kpi / Decimal("100"))
    deductions_total, deduction_items = await approved_deduction_amount(session, employee_id, year, month, base_salary)
    expected_salary = money(base_salary + kpi_bonus - deductions_total)
    return {
        "period": {"year": year, "month": month},
        "employee": {
            "id": employee.id,
            "full_name": f"{employee.name} {employee.surname}".strip(),
        },
        "salary": {
            "base_salary": float(base_salary),
            "max_kpi_fund": float(max_kpi_fund),
            "kpi_bonus": float(kpi_bonus),
            "approved_deductions": float(deductions_total),
            "expected_salary": float(expected_salary),
        },
        "scores": {
            "delivery": score_float(delivery),
            "deadline": score_float(deadline),
            "quality": score_float(quality),
            "team": score_float(team),
            "discipline": score_float(discipline),
            "final_kpi": score_float(final_kpi),
            "weights": {
                "delivery": 35,
                "deadline": 20,
                "quality": 20,
                "team": 15,
                "discipline": 10,
            },
        },
        "details": {
            "delivery": delivery_payload,
            "quality": quality_payload,
            "discipline": discipline_payload,
            "team": team_payload,
            "deductions": deduction_items,
        },
    }


async def freeze_employee_snapshot(session: AsyncSession, employee_id: int, year: int, month: int, created_by: Optional[int] = None) -> int:
    payload = await build_salary_estimate(session, employee_id, year, month, persist_candidates=True)
    salary = payload["salary"]
    scores = payload["scores"]
    now = datetime.utcnow()
    result = await session.execute(
        pg_insert(developer_kpi_salary_snapshot)
        .values(
            employee_id=employee_id,
            period_year=year,
            period_month=month,
            base_salary=salary["base_salary"],
            max_kpi_fund=salary["max_kpi_fund"],
            delivery_score=scores["delivery"],
            deadline_score=scores["deadline"],
            quality_score=scores["quality"],
            team_score=scores["team"],
            discipline_score=scores["discipline"],
            final_kpi=scores["final_kpi"],
            kpi_bonus=salary["kpi_bonus"],
            approved_deductions=salary["approved_deductions"],
            expected_salary=salary["expected_salary"],
            source_payload=json.dumps(payload, ensure_ascii=False, default=date_json),
            frozen_at=now,
            created_by=created_by,
        )
        .on_conflict_do_update(
            index_elements=[
                developer_kpi_salary_snapshot.c.employee_id,
                developer_kpi_salary_snapshot.c.period_year,
                developer_kpi_salary_snapshot.c.period_month,
            ],
            set_={
                "base_salary": salary["base_salary"],
                "max_kpi_fund": salary["max_kpi_fund"],
                "delivery_score": scores["delivery"],
                "deadline_score": scores["deadline"],
                "quality_score": scores["quality"],
                "team_score": scores["team"],
                "discipline_score": scores["discipline"],
                "final_kpi": scores["final_kpi"],
                "kpi_bonus": salary["kpi_bonus"],
                "approved_deductions": salary["approved_deductions"],
                "expected_salary": salary["expected_salary"],
                "source_payload": json.dumps(payload, ensure_ascii=False, default=date_json),
                "frozen_at": now,
                "created_by": created_by,
            },
        )
        .returning(developer_kpi_salary_snapshot.c.id)
    )
    return result.scalar_one()


async def auto_freeze_previous_month_snapshots() -> dict:
    await ensure_developer_kpi_schema()
    today = datetime.now(TASHKENT_TZ).date()
    last_day = calendar.monthrange(today.year, today.month)[1]
    if today.day != last_day:
        return {"skipped": True, "reason": "not_month_end"}
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(user.c.id).where(and_(user.c.is_active == True, user.c.role == UserRole.member))
        )).fetchall()
        frozen = 0
        for row in rows:
            await freeze_employee_snapshot(session, row.id, today.year, today.month)
            frozen += 1
        await session.commit()
        return {"skipped": False, "year": today.year, "month": today.month, "frozen_count": frozen}


@router.post("/work-schedules", summary="Developer ish vaqti qo'shish yoki yangilash")
async def upsert_work_schedule(payload: DeveloperWorkScheduleRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    await ensure_member(session, payload.user_id)
    now = datetime.utcnow()
    values = payload.model_dump()
    values.update({"created_by": current_user.id, "created_at": now, "updated_at": now})
    result = await session.execute(
        pg_insert(developer_work_schedule)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[developer_work_schedule.c.user_id, developer_work_schedule.c.weekday],
            set_={
                "work_start_time": payload.work_start_time,
                "work_end_time": payload.work_end_time,
                "free_start_time": payload.free_start_time,
                "free_end_time": payload.free_end_time,
                "late_grace_minutes": payload.late_grace_minutes,
                "is_active": payload.is_active,
                "updated_at": now,
            },
        )
        .returning(developer_work_schedule.c.id)
    )
    schedule_id = result.scalar_one()
    await session.commit()
    return {"success": True, "id": schedule_id}


@router.get("/work-schedules", summary="Developer ish vaqtlari")
async def list_work_schedules(user_id: Optional[int] = None, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    query = (
        select(developer_work_schedule, user.c.name, user.c.surname)
        .select_from(developer_work_schedule.join(user, developer_work_schedule.c.user_id == user.c.id))
        .order_by(developer_work_schedule.c.user_id.asc(), developer_work_schedule.c.weekday.asc())
    )
    if user_id is not None:
        query = query.where(developer_work_schedule.c.user_id == user_id)
    rows = (await session.execute(query)).fetchall()
    return {
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "full_name": f"{row.name} {row.surname}".strip(),
                "weekday": row.weekday,
                "work_start_time": row.work_start_time.isoformat(),
                "work_end_time": row.work_end_time.isoformat(),
                "free_start_time": row.free_start_time.isoformat() if row.free_start_time else None,
                "free_end_time": row.free_end_time.isoformat() if row.free_end_time else None,
                "late_grace_minutes": row.late_grace_minutes,
                "is_active": row.is_active,
            }
            for row in rows
        ]
    }


@router.patch("/projects/{project_id}/delivery", summary="Project delivery KPI ma'lumotlarini yangilash")
async def update_project_delivery(project_id: int, payload: DeveloperProjectDeliveryRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    project_row = await ensure_project(session, project_id)
    delay_days = None
    if payload.actual_delivery_date and project_row.deadline:
        planned_day = project_row.deadline.date() if isinstance(project_row.deadline, datetime) else project_row.deadline
        delay_days = business_delay_days(planned_day, datetime.combine(payload.actual_delivery_date, time.min), payload.approved_blocked_days)
    await session.execute(
        update(project)
        .where(project.c.id == project_id)
        .values(
            actual_delivery_date=payload.actual_delivery_date,
            delivery_status=payload.delivery_status.strip() if payload.delivery_status else None,
            approved_blocked_days=payload.approved_blocked_days,
            real_delay_days=delay_days,
            updated_at=datetime.utcnow(),
        )
    )
    await session.commit()
    return {"success": True, "project_id": project_id, "real_delay_days": delay_days}


@router.post("/features", summary="KPI feature yaratish")
async def create_feature(payload: DeveloperKpiFeatureCreateRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    await ensure_project(session, payload.project_id)
    await ensure_member(session, payload.owner_id)
    if payload.frontend_percent + payload.backend_percent != 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="frontend_percent + backend_percent = 100 bo'lishi kerak")
    now = datetime.utcnow()
    result = await session.execute(
        insert(developer_kpi_feature)
        .values(
            project_id=payload.project_id,
            title=payload.title.strip(),
            description=payload.description,
            acceptance_criteria=payload.acceptance_criteria,
            points=payload.points,
            owner_id=payload.owner_id,
            frontend_percent=payload.frontend_percent,
            backend_percent=payload.backend_percent,
            due_date=payload.due_date,
            status=payload.status,
            is_mandatory=payload.is_mandatory,
            is_locked=payload.lock_now,
            locked_at=now if payload.lock_now else None,
            locked_by=current_user.id if payload.lock_now else None,
            created_by=current_user.id,
            created_at=now,
            updated_at=now,
        )
        .returning(developer_kpi_feature.c.id)
    )
    feature_id = result.scalar_one()
    await session.commit()
    return {"success": True, "id": feature_id}


@router.get("/features", summary="KPI feature list")
async def list_features(
    project_id: Optional[int] = None,
    owner_id: Optional[int] = None,
    year: Optional[int] = Query(default=None, ge=2020, le=2035),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=300),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_developer_kpi_schema()
    conditions = []
    if project_id is not None:
        conditions.append(developer_kpi_feature.c.project_id == project_id)
    if owner_id is not None:
        conditions.append(developer_kpi_feature.c.owner_id == owner_id)
    if year is not None and month is not None:
        start_day, end_day = month_range(year, month)
        conditions.append(developer_kpi_feature.c.due_date >= start_day)
        conditions.append(developer_kpi_feature.c.due_date <= end_day)
    if status_filter:
        conditions.append(developer_kpi_feature.c.status == status_filter)
    total_query = select(func.count()).select_from(developer_kpi_feature)
    query = (
        select(
            developer_kpi_feature,
            project.c.project_name,
            user.c.name.label("owner_name"),
            user.c.surname.label("owner_surname"),
        )
        .select_from(
            developer_kpi_feature
            .join(project, developer_kpi_feature.c.project_id == project.c.id)
            .join(user, developer_kpi_feature.c.owner_id == user.c.id)
        )
        .order_by(developer_kpi_feature.c.due_date.desc(), developer_kpi_feature.c.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if conditions:
        total_query = total_query.where(and_(*conditions))
        query = query.where(and_(*conditions))
    total_count = (await session.execute(total_query)).scalar_one()
    rows = (await session.execute(query)).fetchall()
    return {"items": [feature_payload(row) for row in rows], "page": page, "page_size": page_size, "total_count": total_count}


@router.patch("/features/{feature_id}", summary="KPI feature update")
async def update_feature(feature_id: int, payload: DeveloperKpiFeatureUpdateRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    existing = (await session.execute(select(developer_kpi_feature).where(developer_kpi_feature.c.id == feature_id))).fetchone()
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feature topilmadi")
    if existing.is_locked and not is_ceo(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Locked feature faqat CEO tomonidan o'zgaradi")
    update_data = payload.model_dump(exclude_unset=True)
    if "frontend_percent" in update_data or "backend_percent" in update_data:
        frontend_percent = update_data.get("frontend_percent", existing.frontend_percent)
        backend_percent = update_data.get("backend_percent", existing.backend_percent)
        if frontend_percent + backend_percent != 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="frontend_percent + backend_percent = 100 bo'lishi kerak")
    if update_data.get("is_locked") and not existing.is_locked:
        update_data["locked_at"] = datetime.utcnow()
        update_data["locked_by"] = current_user.id
    update_data["updated_at"] = datetime.utcnow()
    await session.execute(update(developer_kpi_feature).where(developer_kpi_feature.c.id == feature_id).values(**update_data))
    await session.commit()
    return {"success": True, "id": feature_id}


@router.post("/features/{feature_id}/accept", summary="Feature qabul qilish")
async def accept_feature(feature_id: int, payload: DeveloperKpiFeatureAcceptRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    row = (await session.execute(select(developer_kpi_feature).where(developer_kpi_feature.c.id == feature_id))).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feature topilmadi")
    now = datetime.utcnow()
    await session.execute(
        update(developer_kpi_feature)
        .where(developer_kpi_feature.c.id == feature_id)
        .values(status="accepted", accepted_at=payload.accepted_at or now, accepted_by=current_user.id, is_locked=True, locked_at=row.locked_at or now, locked_by=row.locked_by or current_user.id, updated_at=now)
    )
    await session.commit()
    return {"success": True, "id": feature_id}


@router.delete("/features/{feature_id}", summary="Feature o'chirish")
async def delete_feature(feature_id: int, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    row = (await session.execute(select(developer_kpi_feature).where(developer_kpi_feature.c.id == feature_id))).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feature topilmadi")
    if row.is_locked and not is_ceo(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Locked feature faqat CEO o'chiradi")
    await session.execute(delete(developer_kpi_feature).where(developer_kpi_feature.c.id == feature_id))
    await session.commit()
    return {"success": True, "id": feature_id}


@router.post("/blocked-periods", summary="Blocked period yaratish")
async def create_blocked_period(payload: DeveloperKpiBlockedPeriodRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_project(session, payload.project_id)
    await ensure_member(session, payload.employee_id)
    now = datetime.utcnow()
    result = await session.execute(insert(developer_kpi_blocked_period).values(**payload.model_dump(), created_by=current_user.id, created_at=now, updated_at=now).returning(developer_kpi_blocked_period.c.id))
    blocked_id = result.scalar_one()
    await session.commit()
    return {"success": True, "id": blocked_id}


@router.get("/blocked-periods", summary="Blocked period list")
async def list_blocked_periods(project_id: Optional[int] = None, employee_id: Optional[int] = None, approval_status: Optional[str] = None, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    conditions = []
    if project_id is not None:
        conditions.append(developer_kpi_blocked_period.c.project_id == project_id)
    if employee_id is not None:
        conditions.append(developer_kpi_blocked_period.c.employee_id == employee_id)
    if approval_status:
        conditions.append(developer_kpi_blocked_period.c.approval_status == approval_status)
    query = select(developer_kpi_blocked_period).order_by(developer_kpi_blocked_period.c.started_at.desc(), developer_kpi_blocked_period.c.id.desc())
    if conditions:
        query = query.where(and_(*conditions))
    rows = (await session.execute(query)).fetchall()
    return {"items": [dict(row._mapping) for row in rows], "total_count": len(rows)}


@router.patch("/blocked-periods/{blocked_id}", summary="Blocked period update")
async def update_blocked_period(blocked_id: int, payload: DeveloperKpiBlockedPeriodUpdateRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    update_data = payload.model_dump(exclude_unset=True)
    if "approval_status" in update_data:
        await ensure_kpi_manager(current_user)
        if update_data["approval_status"] == "approved":
            update_data["approved_by"] = current_user.id
            update_data["approved_at"] = datetime.utcnow()
    update_data["updated_at"] = datetime.utcnow()
    result = await session.execute(update(developer_kpi_blocked_period).where(developer_kpi_blocked_period.c.id == blocked_id).values(**update_data))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blocked period topilmadi")
    return {"success": True, "id": blocked_id}


@router.post("/quality-events", summary="Quality event yaratish")
async def create_quality_event(payload: DeveloperKpiQualityEventRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    await ensure_project(session, payload.project_id)
    await ensure_member(session, payload.employee_id)
    now = datetime.utcnow()
    result = await session.execute(insert(developer_kpi_quality_event).values(**payload.model_dump(), created_by=current_user.id, created_at=now, updated_at=now).returning(developer_kpi_quality_event.c.id))
    event_id = result.scalar_one()
    await session.commit()
    return {"success": True, "id": event_id}


@router.get("/quality-events", summary="Quality event list")
async def list_quality_events(project_id: Optional[int] = None, employee_id: Optional[int] = None, year: Optional[int] = Query(default=None, ge=2020, le=2035), month: Optional[int] = Query(default=None, ge=1, le=12), session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    conditions = []
    if project_id is not None:
        conditions.append(developer_kpi_quality_event.c.project_id == project_id)
    if employee_id is not None:
        conditions.append(developer_kpi_quality_event.c.employee_id == employee_id)
    if year is not None and month is not None:
        start_day, end_day = month_range(year, month)
        conditions.extend([developer_kpi_quality_event.c.event_date >= start_day, developer_kpi_quality_event.c.event_date <= end_day])
    query = select(developer_kpi_quality_event).order_by(developer_kpi_quality_event.c.event_date.desc(), developer_kpi_quality_event.c.id.desc())
    if conditions:
        query = query.where(and_(*conditions))
    rows = (await session.execute(query)).fetchall()
    return {"items": [dict(row._mapping) for row in rows], "total_count": len(rows)}


@router.patch("/quality-events/{event_id}", summary="Quality event update")
async def update_quality_event(event_id: int, payload: DeveloperKpiQualityEventUpdateRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    update_data = payload.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.utcnow()
    result = await session.execute(update(developer_kpi_quality_event).where(developer_kpi_quality_event.c.id == event_id).values(**update_data))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quality event topilmadi")
    return {"success": True, "id": event_id}


@router.delete("/quality-events/{event_id}", summary="Quality event o'chirish")
async def delete_quality_event(event_id: int, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    result = await session.execute(delete(developer_kpi_quality_event).where(developer_kpi_quality_event.c.id == event_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quality event topilmadi")
    return {"success": True, "id": event_id}


@router.get("/salary-estimate", summary="Developer KPI salary estimate")
async def salary_estimate(employee_id: int, year: int = Query(..., ge=2020, le=2035), month: int = Query(..., ge=1, le=12), session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    if current_user.id != employee_id:
        await ensure_kpi_manager(current_user)
    return await build_salary_estimate(session, employee_id, year, month)


@router.get("/salary-estimates", summary="Developer KPI salary estimates")
async def salary_estimates(year: int = Query(..., ge=2020, le=2035), month: int = Query(..., ge=1, le=12), employee_ids: Optional[str] = None, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    selected_ids = [int(x.strip()) for x in employee_ids.split(",") if x.strip().isdigit()] if employee_ids else []
    query = select(user.c.id, user.c.name, user.c.surname, user.c.default_salary).where(and_(user.c.is_active == True, user.c.role == UserRole.member))
    if selected_ids:
        query = query.where(user.c.id.in_(selected_ids))
    rows = (await session.execute(query.order_by(user.c.name.asc(), user.c.surname.asc()))).fetchall()
    items = []
    totals = {"base_salary": Decimal("0"), "kpi_bonus": Decimal("0"), "approved_deductions": Decimal("0"), "expected_salary": Decimal("0")}
    for row in rows:
        payload = await build_salary_estimate(session, row.id, year, month, persist_candidates=False)
        for key in totals:
            totals[key] += money(payload["salary"][key])
        items.append(payload)
    return {
        "period": {"year": year, "month": month},
        "summary": {key: float(money(value)) for key, value in totals.items()},
        "items": items,
    }


@router.get("/deductions", summary="KPI deduction list")
async def list_deductions(employee_id: Optional[int] = None, year: Optional[int] = Query(default=None, ge=2020, le=2035), month: Optional[int] = Query(default=None, ge=1, le=12), status_filter: Optional[str] = Query(default=None, alias="status"), session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    conditions = []
    if employee_id is not None:
        conditions.append(developer_kpi_deduction.c.employee_id == employee_id)
    if year is not None:
        conditions.append(developer_kpi_deduction.c.period_year == year)
    if month is not None:
        conditions.append(developer_kpi_deduction.c.period_month == month)
    if status_filter:
        conditions.append(developer_kpi_deduction.c.status == status_filter)
    query = select(developer_kpi_deduction).order_by(developer_kpi_deduction.c.created_at.desc(), developer_kpi_deduction.c.id.desc())
    if conditions:
        query = query.where(and_(*conditions))
    rows = (await session.execute(query)).fetchall()
    return {"items": [dict(row._mapping) for row in rows], "total_count": len(rows)}


@router.patch("/deductions/{deduction_id}", summary="KPI deduction approve/reject")
async def update_deduction(deduction_id: int, payload: DeveloperKpiDeductionUpdateRequest, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    update_data = {"status": payload.status, "updated_at": datetime.utcnow()}
    if payload.reason:
        update_data["reason"] = payload.reason
    if payload.status == "approved":
        update_data["approved_by"] = current_user.id
        update_data["approved_at"] = datetime.utcnow()
    result = await session.execute(update(developer_kpi_deduction).where(developer_kpi_deduction.c.id == deduction_id).values(**update_data))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deduction topilmadi")
    return {"success": True, "id": deduction_id}


@router.post("/snapshots/freeze", summary="Monthly KPI salary snapshot freeze")
async def freeze_snapshots(year: int = Query(..., ge=2020, le=2035), month: int = Query(..., ge=1, le=12), employee_id: Optional[int] = None, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    await ensure_kpi_manager(current_user)
    if employee_id is not None:
        await freeze_employee_snapshot(session, employee_id, year, month, current_user.id)
        frozen = 1
    else:
        rows = (await session.execute(select(user.c.id).where(and_(user.c.is_active == True, user.c.role == UserRole.member)))).fetchall()
        frozen = 0
        for row in rows:
            await freeze_employee_snapshot(session, row.id, year, month, current_user.id)
            frozen += 1
    await session.commit()
    return {"success": True, "year": year, "month": month, "frozen_count": frozen}


@router.get("/snapshots", summary="Monthly KPI salary snapshots")
async def list_snapshots(year: int = Query(..., ge=2020, le=2035), month: int = Query(..., ge=1, le=12), employee_id: Optional[int] = None, session: AsyncSession = Depends(get_async_session), current_user=Depends(get_current_active_user)):
    await ensure_developer_kpi_schema()
    if employee_id is not None and current_user.id == employee_id:
        conditions = [developer_kpi_salary_snapshot.c.employee_id == employee_id]
    else:
        await ensure_kpi_manager(current_user)
        conditions = []
        if employee_id is not None:
            conditions.append(developer_kpi_salary_snapshot.c.employee_id == employee_id)
    conditions.extend([developer_kpi_salary_snapshot.c.period_year == year, developer_kpi_salary_snapshot.c.period_month == month])
    rows = (await session.execute(
        select(developer_kpi_salary_snapshot, user.c.name, user.c.surname)
        .select_from(developer_kpi_salary_snapshot.join(user, developer_kpi_salary_snapshot.c.employee_id == user.c.id))
        .where(and_(*conditions))
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )).fetchall()
    return {
        "items": [
            {
                "id": row.id,
                "employee_id": row.employee_id,
                "full_name": f"{row.name} {row.surname}".strip(),
                "period_year": row.period_year,
                "period_month": row.period_month,
                "base_salary": float(row.base_salary),
                "max_kpi_fund": float(row.max_kpi_fund),
                "delivery_score": float(row.delivery_score),
                "deadline_score": float(row.deadline_score),
                "quality_score": float(row.quality_score),
                "team_score": float(row.team_score),
                "discipline_score": float(row.discipline_score),
                "final_kpi": float(row.final_kpi),
                "kpi_bonus": float(row.kpi_bonus),
                "approved_deductions": float(row.approved_deductions),
                "expected_salary": float(row.expected_salary),
                "source_payload": json.loads(row.source_payload),
                "frozen_at": row.frozen_at.isoformat() if row.frozen_at else None,
            }
            for row in rows
        ],
        "total_count": len(rows),
    }
