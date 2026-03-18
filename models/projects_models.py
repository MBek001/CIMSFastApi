import enum
from datetime import datetime

from sqlalchemy import (
    Table,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Date,
    Text,
    ForeignKey,
    UniqueConstraint,
    Enum,
    Index,
)

from models.admin_models import metadata


class CardPriority(enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


project = Table(
    "project",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_name", String(255), nullable=False),
    Column("project_description", Text, nullable=True),
    Column("project_url", String(500), nullable=True),
    Column("project_image", String(500), nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)


project_member = Table(
    "project_member",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow),
    UniqueConstraint("project_id", "user_id", name="uq_project_member"),
)


project_board = Table(
    "project_board",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("description", Text, nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("is_archived", Boolean, default=False),
)


project_board_column = Table(
    "project_board_column",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("board_id", Integer, ForeignKey("project_board.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(80), nullable=False),
    Column("order", Integer, nullable=False, default=0),
    Column("color", String(7), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    UniqueConstraint("board_id", "order", name="uq_project_board_column_order"),
)


project_board_card = Table(
    "project_board_card",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("column_id", Integer, ForeignKey("project_board_column.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=True),
    Column("order", Integer, nullable=False, default=0),
    Column("priority", Enum(CardPriority, name="cardpriority"), default=CardPriority.medium, nullable=False),
    Column("assignee_id", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("due_date", Date, nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    UniqueConstraint("column_id", "order", name="uq_project_board_card_order"),
)


project_board_card_file = Table(
    "project_board_card_file",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("card_id", Integer, ForeignKey("project_board_card.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("url_path", String(255), nullable=False),
)


Index("idx_project_member_project_id", project_member.c.project_id)
Index("idx_project_member_user_id", project_member.c.user_id)
Index("idx_project_board_project_id", project_board.c.project_id)
Index("idx_project_board_column_board_id", project_board_column.c.board_id)
Index("idx_project_board_card_column_id", project_board_card.c.column_id)
Index("idx_project_board_card_assignee_id", project_board_card.c.assignee_id)
Index("idx_project_board_card_file_card_id", project_board_card_file.c.card_id)
