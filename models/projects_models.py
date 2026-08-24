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


class ProjectAttachmentType(enum.Enum):
    tz = "tz"
    kp = "kp"
    contracts = "contracts"


project = Table(
    "project",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("team_id", Integer, ForeignKey("project_team.id", ondelete="SET NULL"), nullable=True),
    Column("project_name", String(255), nullable=False),
    Column("project_description", Text, nullable=True),
    Column("project_url", String(500), nullable=True),
    Column("project_image", String(500), nullable=True),
    Column("deadline", DateTime, nullable=True),
    Column("telegram_group_id", String(100), nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)


project_team = Table(
    "project_team",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("description", Text, nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)


project_team_member = Table(
    "project_team_member",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("team_id", Integer, ForeignKey("project_team.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow),
    UniqueConstraint("team_id", "user_id", name="uq_project_team_member"),
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
    Column("due_date", DateTime, nullable=True),
    Column("completed_at", DateTime, nullable=True),
    Column("telegram_source_chat_id", String(100), nullable=True),
    Column("telegram_source_message_id", String(100), nullable=True),
    Column("telegram_source_command", String(50), nullable=True),
    Column("telegram_source_kind", String(20), nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    UniqueConstraint("column_id", "order", name="uq_project_board_card_order"),
)


project_board_card_status_history = Table(
    "project_board_card_status_history",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("card_id", Integer, ForeignKey("project_board_card.id", ondelete="CASCADE"), nullable=False),
    Column("column_id", Integer, ForeignKey("project_board_column.id", ondelete="SET NULL"), nullable=True),
    Column("column_name", String(80), nullable=False),
    Column("entered_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("left_at", DateTime, nullable=True),
    Column("duration_seconds", Integer, nullable=True),
    Column("moved_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
)


project_board_card_assignee = Table(
    "project_board_card_assignee",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("card_id", Integer, ForeignKey("project_board_card.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow),
    UniqueConstraint("card_id", "user_id", name="uq_project_board_card_assignee"),
)


project_board_card_file = Table(
    "project_board_card_file",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("card_id", Integer, ForeignKey("project_board_card.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("url_path", String(255), nullable=False),
)


project_attachment = Table(
    "project_attachment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("attachment_type", Enum(ProjectAttachmentType, name="projectattachmenttype"), nullable=False),
    Column("file_name", String(255), nullable=False),
    Column("url_path", String(500), nullable=False),
    Column("mime_type", String(255), nullable=True),
    Column("file_size", Integer, nullable=False, default=0),
    Column("description", Text, nullable=True),
    Column("created_by", Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)


Index("idx_project_member_project_id", project_member.c.project_id)
Index("idx_project_member_user_id", project_member.c.user_id)
Index("idx_project_team_member_team_id", project_team_member.c.team_id)
Index("idx_project_team_member_user_id", project_team_member.c.user_id)
Index("idx_project_team_created_by", project_team.c.created_by)
Index("idx_project_team_id", project.c.team_id)
Index("idx_project_deadline", project.c.deadline)
Index("idx_project_telegram_group_id", project.c.telegram_group_id)
Index("idx_project_board_project_id", project_board.c.project_id)
Index("idx_project_board_column_board_id", project_board_column.c.board_id)
Index("idx_project_board_card_column_id", project_board_card.c.column_id)
Index("idx_project_board_card_assignee_id", project_board_card.c.assignee_id)
Index("idx_project_board_card_due_date", project_board_card.c.due_date)
Index("idx_project_board_card_completed_at", project_board_card.c.completed_at)
Index("idx_project_board_card_telegram_source", project_board_card.c.telegram_source_chat_id, project_board_card.c.telegram_source_message_id)
Index("idx_project_card_status_history_card_id", project_board_card_status_history.c.card_id)
Index("idx_project_card_status_history_open", project_board_card_status_history.c.card_id, project_board_card_status_history.c.left_at)
Index("idx_project_board_card_assignee_card_id", project_board_card_assignee.c.card_id)
Index("idx_project_board_card_assignee_user_id", project_board_card_assignee.c.user_id)
Index("idx_project_board_card_file_card_id", project_board_card_file.c.card_id)
Index("idx_project_attachment_project_id", project_attachment.c.project_id)
Index("idx_project_attachment_type", project_attachment.c.attachment_type)
