"""
Instagram Statistics Models
For tracking Instagram follower counts and growth metrics
"""
from sqlalchemy import Table, Column, Integer, String, DateTime, Date, Boolean, Index, MetaData, ForeignKey, UniqueConstraint
from datetime import datetime
from models.admin_models import metadata


# Instagram account configuration
instagram_account = Table(
    "instagram_account",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_username", String(255), nullable=False),
    Column("instagram_business_account_id", String(255), nullable=True),  # IG Business Account ID
    Column("facebook_page_id", String(255), nullable=True),  # Connected FB Page ID
    Column("access_token", String(500), nullable=True),  # Long-lived access token
    Column("token_expires_at", DateTime, nullable=True),
    Column("is_active", Boolean, default=True),
    Column("last_synced_at", DateTime, nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)

# Daily follower count snapshots
instagram_stats = Table(
    "instagram_stats",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("instagram_account.id", ondelete="CASCADE"), nullable=False),
    Column("date", Date, nullable=False, index=True),
    Column("followers_count", Integer, nullable=False),
    Column("following_count", Integer, nullable=True),
    Column("media_count", Integer, nullable=True),  # Total posts
    Column("created_at", DateTime, default=datetime.utcnow),
    UniqueConstraint("account_id", "date", name="uq_instagram_stats_account_date")  # One stat per day per account
)

# Create indexes for performance
Index("idx_instagram_stats_date", instagram_stats.c.date)
Index("idx_instagram_stats_account_date", instagram_stats.c.account_id, instagram_stats.c.date)
