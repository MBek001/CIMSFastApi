from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint

from models.admin_models import metadata


COGNILABSAI_CHAT_PERMISSION = "cognilabsai_chat"
COGNILABSAI_INTEGRATIONS_PERMISSION = "cognilabsai_integrations"


cognilabsai_global_integration = Table(
    "cognilabsai_global_integration",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("openai_api_key", Text, nullable=True),
    Column("openai_model", String(255), nullable=True),
    Column("openai_base_url", String(500), nullable=True),
    Column("system_prompt", Text, nullable=True),
    Column("instagram_access_token", Text, nullable=True),
    Column("instagram_business_id", String(255), nullable=True),
    Column("instagram_verify_token", String(255), nullable=True),
    Column("telegram_api_id", String(100), nullable=True),
    Column("telegram_api_hash", String(255), nullable=True),
    Column("telegram_session", Text, nullable=True),
    Column("websocket_api_key", String(255), nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    extend_existing=True,
)


cognilabsai_conversation = Table(
    "cognilabsai_conversation",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("channel", String(32), nullable=False),
    Column("client_external_id", String(255), nullable=False),
    Column("client_username", String(255), nullable=True),
    Column("client_full_name", String(255), nullable=True),
    Column("client_avatar_url", String(1000), nullable=True),
    Column("instagram_business_id", String(255), nullable=True),
    Column("ai_enabled", Boolean, nullable=False, default=True),
    Column("lead_created", Boolean, nullable=False, default=False),
    Column("crm_customer_id", Integer, nullable=True),
    Column("lead_full_name", String(255), nullable=True),
    Column("lead_phone_number", String(64), nullable=True),
    Column("lead_business_field", String(255), nullable=True),
    Column("lead_scheduled_time", String(255), nullable=True),
    Column("pause_reason", String(64), nullable=True),
    Column("paused_until", DateTime, nullable=True),
    Column("last_message_at", DateTime, nullable=True),
    Column("last_message_preview", Text, nullable=True),
    Column("last_operator_user_id", Integer, nullable=True),
    Column("last_operator_name", String(255), nullable=True),
    Column("is_imported", Boolean, nullable=False, default=False),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    UniqueConstraint("channel", "client_external_id", name="uq_cognilabsai_conversation_channel_client"),
    extend_existing=True,
)


cognilabsai_message = Table(
    "cognilabsai_message",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", Integer, nullable=False),
    Column("channel", String(32), nullable=False),
    Column("sender_type", String(32), nullable=False),
    Column("operator_user_id", Integer, nullable=True),
    Column("operator_name_snapshot", String(255), nullable=True),
    Column("client_external_id", String(255), nullable=True),
    Column("instagram_message_id", String(255), nullable=True),
    Column("telegram_message_id", String(255), nullable=True),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow),
    extend_existing=True,
)


cognilabsai_pause_event = Table(
    "cognilabsai_pause_event",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", Integer, nullable=False),
    Column("action", String(32), nullable=False),
    Column("reason", String(64), nullable=True),
    Column("operator_user_id", Integer, nullable=True),
    Column("operator_name", String(255), nullable=True),
    Column("pause_until", DateTime, nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    extend_existing=True,
)


cognilabsai_import_log = Table(
    "cognilabsai_import_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_file", String(500), nullable=False),
    Column("source_hash", String(128), nullable=False),
    Column("conversation_id", Integer, nullable=True),
    Column("imported_at", DateTime, default=datetime.utcnow),
    UniqueConstraint("source_hash", name="uq_cognilabsai_import_log_hash"),
    extend_existing=True,
)
