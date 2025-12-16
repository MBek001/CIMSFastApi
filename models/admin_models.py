from sqlalchemy import (
    Table, Column, Integer, String, Boolean, DateTime, Date, DECIMAL, Text, Enum, MetaData, ForeignKey, UniqueConstraint
)
import enum
from datetime import datetime


metadata = MetaData()

# Enums for choices (kept for backward compatibility, but now we use dynamic tables)
class CustomerStatus(enum.Enum):
    contacted = "contacted"
    project_started = "project_started"
    continuing = "continuing"
    finished = "finished"
    rejected = "rejected"
    need_to_call = "need_to_call"

class FinanceType(enum.Enum):
    incomer = "incomer"
    outcomer = "outcomer"

class FinanceStatus(enum.Enum):
    one_time = "one_time"
    monthly = "monthly"

class CardType(enum.Enum):
    card1 = "card1"
    card2 = "card2"
    card3 = "card3"

class CurrencyType(enum.Enum):
    UZS = "UZS"
    USD = "USD"

class TransactionStatus(enum.Enum):
    real = "real"
    statistical = "statistical"

class ConversationLanguage(enum.Enum):
        UZ = "uz"
        RU = "ru"
        EN = "en"

class CustomerType(enum.Enum):
    """Customer type for filtering"""
    local = "local"  # Local customers
    international = "international"  # International customers

# 1. Payment table
payment = Table(
    "payment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project", String(100), nullable=False),
    Column("payment", Boolean, default=True)
)

# 2. SiteControl table
site_control = Table(
    "site_control",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("is_site_on", Boolean, default=True)
)

wordpress_project = Table(
    "wordpress_project",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("url", String(500), nullable=True),
    Column("description", Text, nullable=True),
    Column("is_active", Boolean, default=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)

# 3. Customer table
customer = Table(
    "customer",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("full_name", String(500), nullable=False),
    Column("platform", String(255), nullable=False),
    Column("username", String(255), nullable=True),
    Column("phone_number", String(500), nullable=False),
    Column("status", Enum(CustomerStatus), nullable=False),
    Column("status_name", String(100), nullable=True),  # NEW: Dynamic status from customer_status table (optional, for custom statuses)
    Column("type", Enum(CustomerType), nullable=True, default=None),  # NEW: Customer type (local/international)
    Column("assistant_name", String(255), nullable=True),
    Column("notes", Text, nullable=True),
    Column("audio_file_id", String(500), nullable=True),  # Telegram file ID
    Column("audio_url", String(1000), nullable=True),     # (agar kerak bo'lsa)
    Column("conversation_language", Enum(ConversationLanguage), nullable=True, default=ConversationLanguage.UZ),
    Column("created_at", DateTime, nullable=False)
)

# 4. Finance table
finance = Table(
    "finance",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("type", Enum(FinanceType), nullable=False),
    Column("status", Enum(FinanceStatus), nullable=False),
    Column("card", Enum(CardType), nullable=False),
    Column("service", String(255), nullable=False),
    Column("summ", DECIMAL(15, 2), nullable=False),
    Column("currency", Enum(CurrencyType), default=CurrencyType.UZS),
    Column("date", Date, nullable=False),
    Column("donation", DECIMAL(15, 2), default=0),
    Column("donation_percentage", DECIMAL(5, 2), default=0),
    Column("tax_percentage", DECIMAL(5, 2), nullable=True),
    Column("exchange_rate", DECIMAL(15, 2), nullable=True),
    Column("transaction_status", Enum(TransactionStatus), default=TransactionStatus.statistical),
    Column("initial_date", Date, nullable=True)
)

# 5. DonationBalance table
donation_balance = Table(
    "donation_balance",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("total_donation", DECIMAL(15, 2), default=0)
)

# 6. ExchangeRate table
exchange_rate = Table(
    "exchange_rate",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("usd_to_uzs", DECIMAL(15, 2), default=12700.00),
    Column("updated_at", DateTime, nullable=False)
)

# 7. Dynamic Customer Status table (NEW)
customer_status_table = Table(
    "customer_status",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False, unique=True),  # e.g., "contacted", "project_started"
    Column("display_name", String(255), nullable=False),  # e.g., "Contacted", "Project Started"
    Column("description", Text, nullable=True),
    Column("color", String(50), nullable=True),  # For UI color coding
    Column("order", Integer, default=0),  # Display order
    Column("is_active", Boolean, default=True),
    Column("is_system", Boolean, default=False),  # System statuses can't be deleted
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)

# 8. Dynamic User Role table (NEW)
user_role_table = Table(
    "user_role",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False, unique=True),  # e.g., "sales_manager", "ceo"
    Column("display_name", String(255), nullable=False),  # e.g., "Sales Manager", "CEO"
    Column("description", Text, nullable=True),
    Column("is_active", Boolean, default=True),
    Column("is_system", Boolean, default=False),  # System roles can't be deleted
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)

# 9. Sales Manager Assignment table (NEW)
sales_manager_assignment = Table(
    "sales_manager_assignment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("customer_id", Integer, ForeignKey("customer.id", ondelete="CASCADE"), nullable=False),
    Column("sales_manager_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("assigned_at", DateTime, default=datetime.utcnow),
    Column("assigned_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),  # Who assigned (or NULL for auto-assign)
    Column("is_active", Boolean, default=True),
    UniqueConstraint("customer_id", name="uq_customer_assignment")  # One customer can only have one active assignment
)

# 10. Sales Manager Assignment Counter (for round-robin)
sales_manager_counter = Table(
    "sales_manager_counter",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("last_assigned_index", Integer, default=0),  # Track which sales manager was last assigned
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)
