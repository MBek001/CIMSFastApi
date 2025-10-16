from sqlalchemy import (
    Table, Column, Integer, String, Boolean, DateTime, Date, DECIMAL, Text, Enum, MetaData
)
import enum
from datetime import datetime


metadata = MetaData()

# Enums for choices
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
    Column("full_name", String(255), nullable=False),
    Column("platform", String(255), nullable=False),
    Column("username", String(255), nullable=True),
    Column("phone_number", String(20), nullable=False),
    Column("status", Enum(CustomerStatus), nullable=False),
    Column("assistant_name", String(255), nullable=True),
    Column("notes", Text, nullable=True),
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
