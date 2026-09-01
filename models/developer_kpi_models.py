from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    DECIMAL,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)

from models.admin_models import metadata


developer_work_schedule = Table(
    "developer_work_schedule",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("weekday", Integer, nullable=False),
    Column("work_start_time", Time, nullable=False),
    Column("work_end_time", Time, nullable=False),
    Column("free_start_time", Time, nullable=True),
    Column("free_end_time", Time, nullable=True),
    Column("late_grace_minutes", Integer, nullable=False, default=0),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
    UniqueConstraint("user_id", "weekday", name="uq_developer_work_schedule_user_weekday"),
)


developer_kpi_feature = Table(
    "developer_kpi_feature",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("acceptance_criteria", Text, nullable=True),
    Column("points", Integer, nullable=False),
    Column("owner_id", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=False),
    Column("frontend_percent", Integer, nullable=False, default=0),
    Column("backend_percent", Integer, nullable=False, default=100),
    Column("due_date", Date, nullable=False),
    Column("status", String(40), nullable=False, default="planned"),
    Column("is_mandatory", Boolean, nullable=False, default=True),
    Column("is_locked", Boolean, nullable=False, default=False),
    Column("locked_at", DateTime, nullable=True),
    Column("locked_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("accepted_at", DateTime, nullable=True),
    Column("accepted_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("rejected_at", DateTime, nullable=True),
    Column("rejected_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
)


developer_kpi_blocked_period = Table(
    "developer_kpi_blocked_period",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("feature_id", Integer, ForeignKey("developer_kpi_feature.id", ondelete="CASCADE"), nullable=True),
    Column("employee_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("ended_at", DateTime, nullable=True),
    Column("reason", Text, nullable=False),
    Column("dependency", Text, nullable=True),
    Column("evidence_url", Text, nullable=True),
    Column("is_external", Boolean, nullable=False, default=True),
    Column("approval_status", String(20), nullable=False, default="pending"),
    Column("approved_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("approved_at", DateTime, nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
)


developer_kpi_quality_event = Table(
    "developer_kpi_quality_event",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("feature_id", Integer, ForeignKey("developer_kpi_feature.id", ondelete="SET NULL"), nullable=True),
    Column("card_id", Integer, ForeignKey("project_board_card.id", ondelete="SET NULL"), nullable=True),
    Column("employee_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("severity", String(40), nullable=False),
    Column("source", String(40), nullable=False, default="manual"),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("event_date", Date, nullable=False),
    Column("confirmed", Boolean, nullable=False, default=True),
    Column("is_duplicate", Boolean, nullable=False, default=False),
    Column("external_cause", Boolean, nullable=False, default=False),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
)


developer_kpi_deduction = Table(
    "developer_kpi_deduction",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("employee_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=True),
    Column("deduction_type", String(60), nullable=False),
    Column("percent", DECIMAL(5, 2), nullable=False),
    Column("status", String(20), nullable=False, default="candidate"),
    Column("trigger_source", String(120), nullable=False),
    Column("reason", Text, nullable=False),
    Column("period_year", Integer, nullable=False),
    Column("period_month", Integer, nullable=False),
    Column("approved_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("approved_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
    UniqueConstraint("employee_id", "project_id", "deduction_type", "period_year", "period_month", name="uq_developer_kpi_deduction_once"),
)


developer_kpi_salary_snapshot = Table(
    "developer_kpi_salary_snapshot",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("employee_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("period_year", Integer, nullable=False),
    Column("period_month", Integer, nullable=False),
    Column("base_salary", DECIMAL(12, 2), nullable=False),
    Column("max_kpi_fund", DECIMAL(12, 2), nullable=False),
    Column("delivery_score", DECIMAL(6, 2), nullable=False),
    Column("deadline_score", DECIMAL(6, 2), nullable=False),
    Column("quality_score", DECIMAL(6, 2), nullable=False),
    Column("team_score", DECIMAL(6, 2), nullable=False),
    Column("discipline_score", DECIMAL(6, 2), nullable=False),
    Column("final_kpi", DECIMAL(6, 2), nullable=False),
    Column("kpi_bonus", DECIMAL(12, 2), nullable=False),
    Column("approved_deductions", DECIMAL(12, 2), nullable=False),
    Column("expected_salary", DECIMAL(12, 2), nullable=False),
    Column("source_payload", Text, nullable=False),
    Column("frozen_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    UniqueConstraint("employee_id", "period_year", "period_month", name="uq_developer_kpi_salary_snapshot"),
)


Index("idx_developer_work_schedule_user", developer_work_schedule.c.user_id)
Index("idx_developer_kpi_feature_owner_due", developer_kpi_feature.c.owner_id, developer_kpi_feature.c.due_date)
Index("idx_developer_kpi_feature_project", developer_kpi_feature.c.project_id)
Index("idx_developer_kpi_blocked_employee", developer_kpi_blocked_period.c.employee_id, developer_kpi_blocked_period.c.started_at)
Index("idx_developer_kpi_quality_employee", developer_kpi_quality_event.c.employee_id, developer_kpi_quality_event.c.event_date)
Index("idx_developer_kpi_deduction_employee_period", developer_kpi_deduction.c.employee_id, developer_kpi_deduction.c.period_year, developer_kpi_deduction.c.period_month)
Index("idx_developer_kpi_snapshot_period", developer_kpi_salary_snapshot.c.period_year, developer_kpi_salary_snapshot.c.period_month)
