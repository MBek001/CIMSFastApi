import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session_maker
from models.admin_models import app_page_table

from cognilabsai.realtime import manager
from cognilabsai.tables import (
    COGNILABSAI_CHAT_PERMISSION,
    COGNILABSAI_INTEGRATIONS_PERMISSION,
    cognilabsai_conversation,
    cognilabsai_global_integration,
    cognilabsai_import_log,
    cognilabsai_message,
    cognilabsai_pause_event,
)
from cognilabsai.telegram_userbot import telegram_userbot_manager


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_VERIFY_TOKEN = "cognilabsai-verify-token"
DEFAULT_WS_KEY = "cognilabsai-websocket-key"


def utcnow() -> datetime:
    return datetime.utcnow()


def normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


async def ensure_schema(session: AsyncSession):
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS cognilabsai_global_integration (
            id SERIAL PRIMARY KEY,
            openai_api_key TEXT,
            openai_model VARCHAR(255),
            openai_base_url VARCHAR(500),
            system_prompt TEXT,
            instagram_access_token TEXT,
            instagram_business_id VARCHAR(255),
            instagram_verify_token VARCHAR(255),
            telegram_api_id VARCHAR(100),
            telegram_api_hash VARCHAR(255),
            telegram_session TEXT,
            websocket_api_key VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS cognilabsai_conversation (
            id SERIAL PRIMARY KEY,
            channel VARCHAR(32) NOT NULL,
            client_external_id VARCHAR(255) NOT NULL,
            client_username VARCHAR(255),
            client_full_name VARCHAR(255),
            instagram_business_id VARCHAR(255),
            ai_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            pause_reason VARCHAR(64),
            paused_until TIMESTAMP NULL,
            last_message_at TIMESTAMP NULL,
            last_message_preview TEXT NULL,
            last_operator_user_id INTEGER NULL,
            last_operator_name VARCHAR(255) NULL,
            is_imported BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_cognilabsai_conversation_channel_client UNIQUE (channel, client_external_id)
        )
    """))
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS cognilabsai_message (
            id SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            channel VARCHAR(32) NOT NULL,
            sender_type VARCHAR(32) NOT NULL,
            operator_user_id INTEGER NULL,
            operator_name_snapshot VARCHAR(255) NULL,
            client_external_id VARCHAR(255) NULL,
            instagram_message_id VARCHAR(255) NULL,
            telegram_message_id VARCHAR(255) NULL,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS cognilabsai_pause_event (
            id SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            action VARCHAR(32) NOT NULL,
            reason VARCHAR(64) NULL,
            operator_user_id INTEGER NULL,
            operator_name VARCHAR(255) NULL,
            pause_until TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS cognilabsai_import_log (
            id SERIAL PRIMARY KEY,
            source_file VARCHAR(500) NOT NULL,
            source_hash VARCHAR(128) NOT NULL UNIQUE,
            conversation_id INTEGER NULL,
            imported_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await ensure_permission_pages(session)
    await ensure_global_integration_row(session)
    await session.commit()


async def ensure_permission_pages(session: AsyncSession):
    pages = [
        {
            "name": COGNILABSAI_CHAT_PERMISSION,
            "display_name": "CognilabsAI Chat",
            "description": "CognilabsAI chat operations access",
            "route_path": "/cognilabsai/chat",
            "order": 90,
            "is_active": True,
            "is_system": False,
        },
        {
            "name": COGNILABSAI_INTEGRATIONS_PERMISSION,
            "display_name": "CognilabsAI Integrations",
            "description": "CognilabsAI integrations access",
            "route_path": "/cognilabsai/integrations",
            "order": 91,
            "is_active": True,
            "is_system": False,
        },
    ]
    for page in pages:
        existing = await session.execute(select(app_page_table.c.id).where(app_page_table.c.name == page["name"]))
        if existing.scalar() is None:
            await session.execute(insert(app_page_table).values(**page))


async def ensure_global_integration_row(session: AsyncSession):
    result = await session.execute(select(cognilabsai_global_integration.c.id))
    if result.scalar() is None:
        await session.execute(
            insert(cognilabsai_global_integration).values(
                id=1,
                openai_model=DEFAULT_OPENAI_MODEL,
                openai_base_url=DEFAULT_OPENAI_BASE_URL,
                instagram_verify_token=DEFAULT_VERIFY_TOKEN,
                websocket_api_key=DEFAULT_WS_KEY,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )


async def get_integration_config(session: AsyncSession) -> dict:
    await ensure_schema(session)
    result = await session.execute(select(cognilabsai_global_integration))
    row = result.mappings().first()
    return dict(row)


async def update_integration_config(session: AsyncSession, payload: dict) -> dict:
    await ensure_schema(session)
    payload["updated_at"] = utcnow()
    await session.execute(
        update(cognilabsai_global_integration)
        .where(cognilabsai_global_integration.c.id == 1)
        .values(**payload)
    )
    await session.commit()
    await telegram_userbot_manager.restart()
    return await get_integration_config(session)


async def verify_websocket_api_key(session: AsyncSession, api_key: str) -> bool:
    config = await get_integration_config(session)
    expected = config.get("websocket_api_key")
    return bool(expected and api_key == expected)


async def list_conversations(session: AsyncSession, channel: Optional[str] = None) -> list[dict]:
    await ensure_schema(session)
    await refresh_expired_pauses(session)
    query = select(cognilabsai_conversation).order_by(
        cognilabsai_conversation.c.last_message_at.desc().nullslast(),
        cognilabsai_conversation.c.updated_at.desc(),
    )
    if channel:
        query = query.where(cognilabsai_conversation.c.channel == channel)
    result = await session.execute(query)
    return [dict(row) for row in result.mappings().all()]


async def get_conversation(session: AsyncSession, conversation_id: int) -> Optional[dict]:
    await ensure_schema(session)
    result = await session.execute(
        select(cognilabsai_conversation).where(cognilabsai_conversation.c.id == conversation_id)
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_messages(session: AsyncSession, conversation_id: int, limit: int = 200, offset: int = 0) -> list[dict]:
    await ensure_schema(session)
    result = await session.execute(
        select(cognilabsai_message)
        .where(cognilabsai_message.c.conversation_id == conversation_id)
        .order_by(cognilabsai_message.c.created_at.asc(), cognilabsai_message.c.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return [dict(row) for row in result.mappings().all()]


async def refresh_expired_pauses(session: AsyncSession):
    now = utcnow()
    await session.execute(
        update(cognilabsai_conversation)
        .where(
            cognilabsai_conversation.c.ai_enabled == False,
            cognilabsai_conversation.c.pause_reason == "timed",
            cognilabsai_conversation.c.paused_until.is_not(None),
            cognilabsai_conversation.c.paused_until <= now,
        )
        .values(
            ai_enabled=True,
            pause_reason=None,
            paused_until=None,
            updated_at=now,
        )
    )
    await session.commit()


async def upsert_conversation(
    session: AsyncSession,
    *,
    channel: str,
    client_external_id: str,
    client_username: Optional[str] = None,
    client_full_name: Optional[str] = None,
    instagram_business_id: Optional[str] = None,
    is_imported: bool = False,
) -> dict:
    await ensure_schema(session)
    result = await session.execute(
        select(cognilabsai_conversation).where(
            cognilabsai_conversation.c.channel == channel,
            cognilabsai_conversation.c.client_external_id == client_external_id,
        )
    )
    existing = result.mappings().first()
    if existing:
        updates = {"updated_at": utcnow()}
        if client_username:
            updates["client_username"] = client_username
        if client_full_name:
            updates["client_full_name"] = client_full_name
        if instagram_business_id:
            updates["instagram_business_id"] = instagram_business_id
        if is_imported:
            updates["is_imported"] = True
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == existing["id"])
            .values(**updates)
        )
        await session.commit()
        return await get_conversation(session, existing["id"])

    insert_result = await session.execute(
        insert(cognilabsai_conversation).values(
            channel=channel,
            client_external_id=client_external_id,
            client_username=client_username,
            client_full_name=client_full_name,
            instagram_business_id=instagram_business_id,
            ai_enabled=True,
            pause_reason=None,
            paused_until=None,
            last_message_at=None,
            last_message_preview=None,
            last_operator_user_id=None,
            last_operator_name=None,
            is_imported=is_imported,
            created_at=utcnow(),
            updated_at=utcnow(),
        ).returning(cognilabsai_conversation.c.id)
    )
    conversation_id = insert_result.scalar_one()
    await session.commit()
    return await get_conversation(session, conversation_id)


async def get_or_create_conversation(
    session: AsyncSession,
    **kwargs,
) -> tuple[dict, bool]:
    result = await session.execute(
        select(cognilabsai_conversation).where(
            cognilabsai_conversation.c.channel == kwargs["channel"],
            cognilabsai_conversation.c.client_external_id == kwargs["client_external_id"],
        )
    )
    existing = result.mappings().first()
    conversation = await upsert_conversation(session, **kwargs)
    return conversation, existing is None


async def create_message(
    session: AsyncSession,
    *,
    conversation_id: int,
    channel: str,
    sender_type: str,
    text_value: str,
    operator_user_id: Optional[int] = None,
    operator_name_snapshot: Optional[str] = None,
    client_external_id: Optional[str] = None,
    instagram_message_id: Optional[str] = None,
    telegram_message_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> dict:
    ts = normalize_datetime(created_at) or utcnow()
    result = await session.execute(
        insert(cognilabsai_message).values(
            conversation_id=conversation_id,
            channel=channel,
            sender_type=sender_type,
            operator_user_id=operator_user_id,
            operator_name_snapshot=operator_name_snapshot,
            client_external_id=client_external_id,
            instagram_message_id=instagram_message_id,
            telegram_message_id=telegram_message_id,
            text=text_value,
            created_at=ts,
        ).returning(cognilabsai_message.c.id)
    )
    message_id = result.scalar_one()
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(
            last_message_at=ts,
            last_message_preview=text_value[:1000],
            updated_at=utcnow(),
        )
    )
    await session.commit()
    message = await get_message_by_id(session, message_id)
    await manager.broadcast(
        {
            "type": "message.created",
            "conversation_id": conversation_id,
            "message": message,
        },
        conversation_id=conversation_id,
    )
    return message


async def get_message_by_id(session: AsyncSession, message_id: int) -> dict:
    result = await session.execute(
        select(cognilabsai_message).where(cognilabsai_message.c.id == message_id)
    )
    return dict(result.mappings().first())


async def set_conversation_pause(
    session: AsyncSession,
    *,
    conversation_id: int,
    ai_enabled: bool,
    reason: Optional[str],
    paused_until: Optional[datetime],
    operator_user_id: Optional[int],
    operator_name: Optional[str],
    action: str,
):
    values = {
        "ai_enabled": ai_enabled,
        "pause_reason": reason,
        "paused_until": normalize_datetime(paused_until),
        "updated_at": utcnow(),
    }
    if operator_user_id is not None:
        values["last_operator_user_id"] = operator_user_id
    if operator_name is not None:
        values["last_operator_name"] = operator_name
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(**values)
    )
    await session.execute(
        insert(cognilabsai_pause_event).values(
            conversation_id=conversation_id,
            action=action,
            reason=reason,
            operator_user_id=operator_user_id,
            operator_name=operator_name,
            pause_until=normalize_datetime(paused_until),
            created_at=utcnow(),
        )
    )
    await session.commit()
    conversation = await get_conversation(session, conversation_id)
    await manager.broadcast(
        {
            "type": "conversation.updated",
            "conversation": conversation,
        },
        conversation_id=conversation_id,
    )
    return conversation


async def send_instagram_message(access_token: str, recipient_id: str, text_value: str) -> str | None:
    url = "https://graph.facebook.com/v21.0/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text_value},
        "messaging_type": "RESPONSE",
        "access_token": access_token,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("message_id")


async def send_operator_message(session: AsyncSession, conversation_id: int, text_value: str, current_user) -> dict:
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    operator_name = " ".join(value for value in [getattr(current_user, "name", None), getattr(current_user, "surname", None)] if value) or getattr(current_user, "email", None)
    if conversation["channel"] == "instagram":
        config = await get_integration_config(session)
        access_token = config.get("instagram_access_token")
        if not access_token:
            raise RuntimeError("Instagram access token is not configured")
        instagram_message_id = await send_instagram_message(access_token, conversation["client_external_id"], text_value)
        message = await create_message(
            session,
            conversation_id=conversation_id,
            channel="instagram",
            sender_type="operator",
            text_value=text_value,
            operator_user_id=current_user.id,
            operator_name_snapshot=operator_name,
            client_external_id=conversation["client_external_id"],
            instagram_message_id=instagram_message_id,
        )
    elif conversation["channel"] == "telegram":
        telegram_message_id = await telegram_userbot_manager.send_message(conversation["client_external_id"], text_value)
        message = await create_message(
            session,
            conversation_id=conversation_id,
            channel="telegram",
            sender_type="operator",
            text_value=text_value,
            operator_user_id=current_user.id,
            operator_name_snapshot=operator_name,
            client_external_id=conversation["client_external_id"],
            telegram_message_id=telegram_message_id,
        )
    else:
        raise RuntimeError("Unsupported channel")

    updated_conversation = await set_conversation_pause(
        session,
        conversation_id=conversation_id,
        ai_enabled=False,
        reason="operator",
        paused_until=None,
        operator_user_id=current_user.id,
        operator_name=operator_name,
        action="pause",
    )
    return {"message": message, "conversation": updated_conversation}


async def build_openai_messages(session: AsyncSession, conversation_id: int) -> list[dict]:
    config = await get_integration_config(session)
    history = await get_messages(session, conversation_id, limit=30, offset=0)
    prompt = config.get("system_prompt") or ""
    messages = []
    if prompt:
        messages.append({"role": "system", "content": prompt})
    for item in history:
        role = "user"
        if item["sender_type"] == "ai":
            role = "assistant"
        elif item["sender_type"] == "operator":
            role = "assistant"
        messages.append({"role": role, "content": item["text"]})
    return messages


async def generate_ai_reply(session: AsyncSession, conversation_id: int) -> Optional[str]:
    config = await get_integration_config(session)
    api_key = config.get("openai_api_key")
    if not api_key:
        return None
    base_url = (config.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    model = config.get("openai_model") or DEFAULT_OPENAI_MODEL
    payload = {
        "model": model,
        "messages": await build_openai_messages(session, conversation_id),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return None
    return (choices[0].get("message") or {}).get("content")


async def maybe_send_ai_reply(session: AsyncSession, conversation_id: int):
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        return None
    if not conversation["ai_enabled"]:
        if conversation.get("pause_reason") == "timed" and conversation.get("paused_until") and normalize_datetime(conversation["paused_until"]) <= utcnow():
            await set_conversation_pause(
                session,
                conversation_id=conversation_id,
                ai_enabled=True,
                reason=None,
                paused_until=None,
                operator_user_id=None,
                operator_name=None,
                action="resume",
            )
        else:
            return None
    reply_text = await generate_ai_reply(session, conversation_id)
    if not reply_text:
        return None
    config = await get_integration_config(session)
    if conversation["channel"] == "instagram":
        access_token = config.get("instagram_access_token")
        if not access_token:
            return None
        instagram_message_id = await send_instagram_message(access_token, conversation["client_external_id"], reply_text)
        return await create_message(
            session,
            conversation_id=conversation_id,
            channel="instagram",
            sender_type="ai",
            text_value=reply_text,
            client_external_id=conversation["client_external_id"],
            instagram_message_id=instagram_message_id,
        )
    if conversation["channel"] == "telegram":
        telegram_message_id = await telegram_userbot_manager.send_message(conversation["client_external_id"], reply_text)
        return await create_message(
            session,
            conversation_id=conversation_id,
            channel="telegram",
            sender_type="ai",
            text_value=reply_text,
            client_external_id=conversation["client_external_id"],
            telegram_message_id=telegram_message_id,
        )
    return None


async def process_instagram_webhook_payload(session: AsyncSession, payload: dict):
    await ensure_schema(session)
    entries = payload.get("entry") or []
    for entry in entries:
        messaging_items = entry.get("messaging") or []
        for item in messaging_items:
            message_data = item.get("message") or {}
            if item.get("sender", {}).get("id") == item.get("recipient", {}).get("id"):
                continue
            if message_data.get("is_echo"):
                continue
            text_value = message_data.get("text")
            if not text_value:
                continue
            sender_id = str(item["sender"]["id"])
            recipient_id = str(item["recipient"]["id"])
            conversation = await upsert_conversation(
                session,
                channel="instagram",
                client_external_id=sender_id,
                instagram_business_id=recipient_id,
            )
            await create_message(
                session,
                conversation_id=conversation["id"],
                channel="instagram",
                sender_type="client",
                text_value=text_value,
                client_external_id=sender_id,
                instagram_message_id=message_data.get("mid"),
            )
            await maybe_send_ai_reply(session, conversation["id"])


async def process_telegram_userbot_message(
    *,
    peer_id: str,
    sender_id: Optional[str],
    text: str,
    username: Optional[str],
    full_name: Optional[str],
):
    if not text:
        return
    async with async_session_maker() as session:
        await ensure_schema(session)
        conversation = await upsert_conversation(
            session,
            channel="telegram",
            client_external_id=peer_id,
            client_username=username,
            client_full_name=full_name,
        )
        await create_message(
            session,
            conversation_id=conversation["id"],
            channel="telegram",
            sender_type="client",
            text_value=text,
            client_external_id=sender_id or peer_id,
        )
        await maybe_send_ai_reply(session, conversation["id"])


async def start_telegram_outbound_conversation(session: AsyncSession, peer: str, text_value: str, current_user) -> dict:
    snapshot = await telegram_userbot_manager.resolve_peer_snapshot(peer)
    conversation = await upsert_conversation(
        session,
        channel="telegram",
        client_external_id=snapshot["external_id"],
        client_username=snapshot.get("username"),
        client_full_name=snapshot.get("full_name"),
    )
    operator_name = " ".join(value for value in [getattr(current_user, "name", None), getattr(current_user, "surname", None)] if value) or getattr(current_user, "email", None)
    telegram_message_id = await telegram_userbot_manager.send_message(peer, text_value)
    message = await create_message(
        session,
        conversation_id=conversation["id"],
        channel="telegram",
        sender_type="operator",
        text_value=text_value,
        operator_user_id=current_user.id,
        operator_name_snapshot=operator_name,
        client_external_id=snapshot["external_id"],
        telegram_message_id=telegram_message_id,
    )
    updated_conversation = await set_conversation_pause(
        session,
        conversation_id=conversation["id"],
        ai_enabled=False,
        reason="operator",
        paused_until=None,
        operator_user_id=current_user.id,
        operator_name=operator_name,
        action="pause",
    )
    return {"conversation": updated_conversation, "message": message}


def parse_conversation_line(line: str):
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (Client|AI|Operator): ?(.*)$", line.rstrip("\n"))
    if not match:
        return None
    created_at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    role = match.group(2)
    text_value = match.group(3)
    sender_type = {
        "Client": "client",
        "AI": "ai",
        "Operator": "operator",
    }[role]
    return created_at, sender_type, text_value


async def import_instagram_conversations(session: AsyncSession, folder_path: str) -> dict:
    await ensure_schema(session)
    imported_files = 0
    skipped_files = 0
    created_conversations = 0
    created_messages = 0
    for file_name in sorted(os.listdir(folder_path)):
        full_path = os.path.join(folder_path, file_name)
        if not os.path.isfile(full_path):
            continue
        file_hash = hashlib.sha256(f"{file_name}:{os.path.getsize(full_path)}:{os.path.getmtime(full_path)}".encode()).hexdigest()
        existing_log = await session.execute(
            select(cognilabsai_import_log.c.id).where(cognilabsai_import_log.c.source_hash == file_hash)
        )
        if existing_log.scalar() is not None:
            skipped_files += 1
            continue
        if "_" not in file_name:
            skipped_files += 1
            continue
        receiver_id, sender_part = file_name.rsplit("_", 1)
        sender_id = sender_part.replace(".txt", "")
        conversation, was_created = await get_or_create_conversation(
            session,
            channel="instagram",
            client_external_id=sender_id,
            instagram_business_id=receiver_id,
            is_imported=True,
        )
        if was_created:
            created_conversations += 1
        with open(full_path, "r", encoding="utf-8") as source:
            buffered_text = None
            buffered_created_at = None
            buffered_sender_type = None
            for raw_line in source:
                parsed = parse_conversation_line(raw_line)
                if parsed is None:
                    if buffered_text is not None:
                        buffered_text += "\n" + raw_line.rstrip("\n")
                    continue
                if buffered_text is not None:
                    await create_message(
                        session,
                        conversation_id=conversation["id"],
                        channel="instagram",
                        sender_type=buffered_sender_type,
                        text_value=buffered_text,
                        client_external_id=sender_id,
                        created_at=buffered_created_at,
                    )
                    created_messages += 1
                buffered_created_at, buffered_sender_type, buffered_text = parsed
            if buffered_text is not None:
                await create_message(
                    session,
                    conversation_id=conversation["id"],
                    channel="instagram",
                    sender_type=buffered_sender_type,
                    text_value=buffered_text,
                    client_external_id=sender_id,
                    created_at=buffered_created_at,
                )
                created_messages += 1
        await session.execute(
            insert(cognilabsai_import_log).values(
                source_file=full_path,
                source_hash=file_hash,
                conversation_id=conversation["id"],
                imported_at=utcnow(),
            )
        )
        await session.commit()
        imported_files += 1
    return {
        "imported_files": imported_files,
        "skipped_files": skipped_files,
        "created_conversations": created_conversations,
        "created_messages": created_messages,
    }


async def startup_cognilabsai():
    async with async_session_maker() as session:
        await ensure_schema(session)
    await telegram_userbot_manager.start()


async def shutdown_cognilabsai():
    await telegram_userbot_manager.stop()
