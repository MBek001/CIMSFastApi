from sqlalchemy import (
    Table,UniqueConstraint,
    Column, Integer, String, Boolean, DateTime, Date, Time, DECIMAL, Text, Enum, ForeignKey, MetaData, Index
)
import enum
from datetime import datetime

from models.admin_models import metadata


# --- ENUMS --- (kept for backward compatibility, but now we use dynamic role table)
class UserRole(enum.Enum):
    CEO = "CEO"
    financial_director = "Financial Director"
    general_manager = "General Manager"
    member = "Member"
    customer = "Customer"
    sales_manager = "Sales Manager"  # NEW: Sales Manager role


class MistakeCategory(enum.Enum):
    ai_integration = "AI Integration"
    backend = "Backend"
    frontend = "Frontend"
    mobile = "Mobile"
    devops = "DevOps"
    security = "Security"
    performance = "Performance"
    client_impact = "Client Impact"


class MistakeSeverity(enum.Enum):
    minor = "Minor"
    moderate = "Moderate"
    major = "Major"
    critical = "Critical"


class CompensationBonusType(enum.Enum):
    early_delivery = "early_delivery"
    major_early_delivery = "major_early_delivery"

# -- UserPagePermission table --
class PageName(enum.Enum):
    ceo = "ceo"
    payment_list = "payment_list"
    project_toggle = "project_toggle"
    projects = "projects"
    crm = "crm"
    finance_list = "finance_list"
    update_list = "update_list"
    company_payments = "company_payments"



# -- User table --
user = Table(
    "user",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(255), unique=True, nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("surname", String(255), nullable=False),
    Column("password", String(255), nullable=False),
    Column("company_code", String(255), default="oddiy"),
    Column("telegram_id", String(50), nullable=True),
    Column("chat_id", String(64), nullable=True),
    Column("default_salary", DECIMAL(10, 2), default=0.00),
    Column("role", Enum(UserRole), default=UserRole.customer),
    Column("role_name", String(100), nullable=True),  # NEW: Dynamic role from user_role table (optional, for custom roles)
    Column("job_title", String(100), nullable=True),
    Column("profile_image", String(500), nullable=True),
    Column("is_active", Boolean, default=True),
    Column("is_admin", Boolean, default=False),
    Column("is_staff", Boolean, default=False),
    Column("is_superuser", Boolean, default=False)
)

user_page_permission = Table(
    "user_page_permission",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("page_name", String(100), nullable=False),
    UniqueConstraint("user_id", "page_name", name="uq_user_page_permission")
)


# -- CreditCard table --
credit_card = Table(
    "credit_card",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("card_number", String(16), unique=True, nullable=False),
    Column("is_primary", Boolean, default=False),
    Column("is_active", Boolean, default=True),
)

# -- MonthlyUpdate table --
monthly_update = Table(
    "monthly_update",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("year", Integer, nullable=False),                     # 📅 Yil (masalan 2025)
    Column("month", String(20), nullable=False),                  # 📆 Oy nomi yoki raqam (masalan 'September' yoki '09')
    Column("update_date", Date, nullable=False),                  # Qachon kiritilgan
    Column("update_percentage", DECIMAL(5, 2), default=0.00),
    Column("potential_monthly", DECIMAL(10, 2), default=0.00),
    Column("salary_amount", DECIMAL(10, 2), default=0.00),        # 💰 Oylik miqdor
    Column("next_payment_date", Date, nullable=True),
    Column('note', String(500),nullable=True)
)

# -- MonthlyPenalty table --
monthly_penalty = Table(
    "monthly_penalty",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),                      # 1..12
    Column("penalty_points", DECIMAL(6, 2), nullable=False),      # 0..100+
    Column("reason", String(500), nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False)
)

Index("idx_monthly_penalty_user_year_month", monthly_penalty.c.user_id, monthly_penalty.c.year, monthly_penalty.c.month)

# -- MonthlyBonus table --
monthly_bonus = Table(
    "monthly_bonus",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),                      # 1..12
    Column("bonus_amount", DECIMAL(12, 2), nullable=False),       # Monetary bonus
    Column("reason", String(500), nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False)
)

Index("idx_monthly_bonus_user_year_month", monthly_bonus.c.user_id, monthly_bonus.c.year, monthly_bonus.c.month)

# -- Compensation mistake incidents table --
compensation_mistake = Table(
    "compensation_mistake",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("employee_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("reviewer_id", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="SET NULL"), nullable=True),
    Column("card_id", Integer, ForeignKey("project_board_card.id", ondelete="SET NULL"), nullable=True),
    Column("category", Enum(MistakeCategory, name="mistakecategory"), nullable=False),
    Column("severity", Enum(MistakeSeverity, name="mistakeseverity"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=False),
    Column("incident_date", Date, nullable=False),
    Column("reached_client", Boolean, nullable=False, default=True),
    Column("unclear_task", Boolean, nullable=False, default=False),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow),
)

Index("idx_compensation_mistake_employee_date", compensation_mistake.c.employee_id, compensation_mistake.c.incident_date)
Index("idx_compensation_mistake_reviewer_date", compensation_mistake.c.reviewer_id, compensation_mistake.c.incident_date)

# -- Delivery bonus records table --
compensation_bonus = Table(
    "compensation_bonus",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("employee_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="SET NULL"), nullable=True),
    Column("card_id", Integer, ForeignKey("project_board_card.id", ondelete="SET NULL"), nullable=True),
    Column("bonus_type", Enum(CompensationBonusType, name="compensationbonustype"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("award_date", Date, nullable=False),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow),
)

Index("idx_compensation_bonus_employee_date", compensation_bonus.c.employee_id, compensation_bonus.c.award_date)

# -- Attendance log table --
attendance_log = Table(
    "attendance_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("employee_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("attendance_date", Date, nullable=False),
    Column("check_in_time", Time, nullable=False),
    Column("check_out_time", Time, nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow),
    UniqueConstraint("employee_id", "attendance_date", name="uq_attendance_log_employee_date"),
)

Index("idx_attendance_log_employee_date", attendance_log.c.employee_id, attendance_log.c.attendance_date)

# -- Message table --
message = Table(
    "message",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sender_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("receiver_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("subject", String(255), nullable=False),
    Column("body", Text, nullable=False),
    Column("sent_at", DateTime, nullable=False)
)

# -- Payment table --
user_payment = Table(
    "user_payment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project", String(100), nullable=False),
    Column("date", Date, nullable=False),
    Column("summ", DECIMAL(19, 2), nullable=False),
    Column("payment", Boolean, default=False)
)


# -- VerificationCode table --
verification_code = Table(
    "verification_code",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False
    ),
    Column("code", String(10), nullable=False),
    Column("type", String(50), nullable=False),  # 'verify_email' yoki 'reset_password'
    UniqueConstraint("user_id", "type", name="unique_user_code")  # ✅ Har user uchun har type unique
)

# -- RefreshToken table (NEW) --
refresh_token = Table(
    "refresh_token",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("token", String(500), nullable=False, unique=True, index=True),
    Column("expires_at", DateTime, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("is_active", Boolean, default=True),
    Column("device_info", String(255), nullable=True)  # Optional: track device/browser
)
