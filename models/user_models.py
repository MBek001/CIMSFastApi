from sqlalchemy import (
    Table, Column, Integer, String, Boolean, DateTime, Date, DECIMAL, Text, Enum, ForeignKey, MetaData
)
import enum

from models.admin_models import metadata


# --- ENUMS ---
class UserRole(enum.Enum):
    CEO = "CEO"
    financial_director = "Financial Director"
    member = "Member"
    customer = "Customer"

# -- UserPagePermission table --
class PageName(enum.Enum):
    ceo = "ceo"
    payment_list = "payment_list"
    project_toggle = "project_toggle"
    crm = "crm"
    finance_list = "finance_list"



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
    Column("default_salary", DECIMAL(10, 2), default=0.00),
    Column("role", Enum(UserRole), default=UserRole.customer),
    Column("is_active", Boolean, default=True),
    Column("is_admin", Boolean, default=False),
    Column("is_staff", Boolean, default=False),
    Column("is_superuser", Boolean, default=False)
)

user_page_permission = Table(
    "user_page_permission",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("page_name", Enum(PageName), nullable=False),
    # unique constraint (user, page_name)
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
    Column("update_date", Date, nullable=False),
    Column("update_percentage", DECIMAL(5, 2), default=0.00),
    Column("potential_monthly", DECIMAL(10, 2), default=0.00)
)

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
