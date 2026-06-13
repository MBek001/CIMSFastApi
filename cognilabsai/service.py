import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import httpx
from sqlalchemy import String, cast, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.request import HTTPXRequest

from database import async_session_maker
from models.admin_models import app_page_table
from models.user_models import user
from utils.page_permissions import ensure_app_page_schema
from schemes.crm_schemes import ConversationLanguageEnum, CustomerAPICreateRequest
from routers.crm import create_customer_api_record

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
from config import (
    COGNILABS_CHANNEL_ID,
    COGNILABS_TELEGRAM_TOKEN,
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_BUSINESS_ID,
    INSTAGRAM_VERIFY_TOKEN,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_SESSION,
)


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_VERIFY_TOKEN = "cognilabsai-verify-token"
DEFAULT_WS_KEY = "cognilabsai-websocket-key"
LEAD_COOLDOWN_HOURS = 24
COGNILABSAI_BEHAVIOR_PROMPT = (
    "When the user provides all needed details (name, business field/job, phone, preferred call time), "
    "you must call the register_customer tool once. Otherwise continue the script and ask only one question per message. "
    "If the user gives a short Uzbekistan number like 991234567, normalize it to +998991234567. "
    "Always greet with 'Assalomu Alaykum' on first reply. Reply in the customer's language. "
    "Do not mention you are an AI unless directly asked. Do not repeat the same confirmation twice. "
    "Do not start replies with a speaker label or name like 'Alisher:', 'Assistant:', or 'Operator:'. "
    "CRITICAL: The client's_job field must be the client's BUSINESS FIELD/INDUSTRY (e.g. 'futbol', 'restoran', 'qurilish', 'savdo'), "
    "NOT the name of the IT system they want. "
    "Always ask 'Qaysi sohada faoliyat yuritasiz?' as a separate step before asking for phone number. "
    "Never skip the business field question even if the client already described the IT system they need. "
    "IMPORTANT: Do NOT call register_customer unless the client has explicitly provided a preferred call time in the conversation. "
    "If scheduled_time is missing or empty, ask for it first: 'Qo\'ng\'iroq uchun qaysi vaqt qulay bo\'ladi?' — then call register_customer only after receiving the answer."
)

FOLLOW_UP_POLL_INTERVAL_SECONDS = 60
follow_up_scheduler_task: Optional[asyncio.Task] = None
schema_ready = False
schema_lock = asyncio.Lock()
FOLLOW_UPS_ENABLED = False
DEFAULT_INSTAGRAM_FOLLOWUP_STEP1_DELAY_MINUTES = 180
DEFAULT_INSTAGRAM_FOLLOWUP_STEP2_DELAY_MINUTES = 360
DEFAULT_INSTAGRAM_FOLLOWUP_STEP3_DELAY_MINUTES = 1200
DEFAULT_INSTAGRAM_FOLLOWUP_STEP1_MESSAGE = "Assalomu alaykum, siz yozgan masala bo'yicha yana bog'lanmoqchi edik. Agar sizga qulay bo'lsa, savollaringizni yozib qoldirishingiz mumkin."
DEFAULT_INSTAGRAM_FOLLOWUP_STEP2_MESSAGE = "Assalomu alaykum, eslatib o'tamiz, agar sizga xizmatlarimiz bo'yicha qo'shimcha ma'lumot kerak bo'lsa, bemalol yozishingiz mumkin."
DEFAULT_INSTAGRAM_FOLLOWUP_STEP3_MESSAGE = "Assalomu alaykum, siz bilan bog'lanish uchun yana bir bor yozdik. Agar hozir ham qiziqish bo'lsa, qulay vaqtingizda javob yozing."
WEBSITE_AI_DISCOUNT_REPLY = "Bu bo'yicha operatorlarimiz sizga to'liq ma'lumot beradi."
FOLLOW_UP_STOP_PHRASES = (
    "yoq",
    "yo'q",
    "yo‘q",
    "kerak emas",
    "qiziq emas",
    "bezovta qilmang",
    "yozmang",
    "to'xtating",
    "to‘xtating",
    "stop",
    "unsubscribe",
    "not interested",
    "no thanks",
    "rahmat",
    "thanks",
    "thank you",
    "spasibo",
    "спасибо",
    "ne nado",
    "не надо",
    "не интересно",
    "не пишите",
    "otmen",
    "отмена",
    "yomon",
    "yoqmadi",
    "yoqmadi",
    "noroziman",
    "noroziman",
    "complaint",
    "bad service",
    "плохо",
    "жалоба",
    "недоволен",
)

INSTAGRAM_RECIPIENT_NOT_FOUND_SUBCODE = 2534014
INSTAGRAM_OUTSIDE_WINDOW_SUBCODE = 2534022


class InstagramSendError(Exception):
    def __init__(self, message: str, *, status_code: int, error_code: Optional[int] = None, error_subcode: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_subcode = error_subcode

    @property
    def is_permanent(self) -> bool:
        return self.error_subcode in {
            INSTAGRAM_RECIPIENT_NOT_FOUND_SUBCODE,
            INSTAGRAM_OUTSIDE_WINDOW_SUBCODE,
        }


def utcnow() -> datetime:
    return datetime.utcnow()


def get_lead_cooldown_deadline(last_lead_created_at: Optional[datetime]) -> Optional[datetime]:
    normalized = normalize_datetime(last_lead_created_at)
    if normalized is None:
        return None
    return normalized + timedelta(hours=LEAD_COOLDOWN_HOURS)


def is_lead_cooldown_active(conversation: Optional[dict], now: Optional[datetime] = None) -> bool:
    if not conversation:
        return False
    last_lead_created_at = conversation.get("last_lead_created_at")
    deadline = get_lead_cooldown_deadline(last_lead_created_at)
    if deadline is None:
        return False
    return deadline > (now or utcnow())


def normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def get_global_follow_up_fields(channel: str) -> tuple[str, str, str]:
    normalized = (channel or "").strip().lower()
    if normalized == "telegram":
        return (
            "telegram_followup_enabled",
            "telegram_followup_delay_minutes",
            "telegram_followup_message",
        )
    return (
        "instagram_followup_enabled",
        "instagram_followup_delay_minutes",
        "instagram_followup_message",
    )


def get_conversation_follow_up_settings(conversation: dict, config: dict) -> tuple[bool, Optional[int], Optional[str]]:
    if not FOLLOW_UPS_ENABLED:
        return False, None, None
    if (conversation.get("channel") or "").strip().lower() == "website_ai":
        return False, None, None
    if not conversation.get("follow_up_enabled"):
        return False, None, None
    mode = (conversation.get("follow_up_mode") or "").strip().lower()
    if mode == "custom":
        delay_minutes = conversation.get("follow_up_delay_minutes")
        message = (conversation.get("follow_up_message") or "").strip() or None
        return bool(delay_minutes and message), delay_minutes, message
    enabled_key, delay_key, message_key = get_global_follow_up_fields(conversation.get("channel") or "")
    enabled = bool(config.get(enabled_key))
    delay_minutes = config.get(delay_key)
    message = (config.get(message_key) or "").strip() or None
    return bool(enabled and delay_minutes and message), delay_minutes, message


def get_default_instagram_follow_up_steps(config: dict) -> list[tuple[int, int, str]]:
    if not FOLLOW_UPS_ENABLED:
        return []
    steps = [
        (
            1,
            bool(config.get("instagram_default_followup_step1_enabled", True)),
            int(config.get("instagram_default_followup_step1_delay_minutes") or DEFAULT_INSTAGRAM_FOLLOWUP_STEP1_DELAY_MINUTES),
            (config.get("instagram_default_followup_step1_message") or DEFAULT_INSTAGRAM_FOLLOWUP_STEP1_MESSAGE).strip(),
        ),
        (
            2,
            bool(config.get("instagram_default_followup_step2_enabled", True)),
            int(config.get("instagram_default_followup_step2_delay_minutes") or DEFAULT_INSTAGRAM_FOLLOWUP_STEP2_DELAY_MINUTES),
            (config.get("instagram_default_followup_step2_message") or DEFAULT_INSTAGRAM_FOLLOWUP_STEP2_MESSAGE).strip(),
        ),
        (
            3,
            bool(config.get("instagram_default_followup_step3_enabled", True)),
            int(config.get("instagram_default_followup_step3_delay_minutes") or DEFAULT_INSTAGRAM_FOLLOWUP_STEP3_DELAY_MINUTES),
            (config.get("instagram_default_followup_step3_message") or DEFAULT_INSTAGRAM_FOLLOWUP_STEP3_MESSAGE).strip(),
        ),
    ]
    return [(step, delay, message) for step, enabled, delay, message in steps if enabled and delay > 0 and message]


def is_default_instagram_follow_up_globally_enabled(config: dict) -> bool:
    if not FOLLOW_UPS_ENABLED:
        return False
    return bool(config.get("instagram_default_followup_enabled"))


def is_default_instagram_follow_up_eligible(conversation: dict) -> bool:
    return (
        (conversation.get("channel") == "instagram")
        and not conversation.get("is_imported")
        and not conversation.get("crm_customer_id")
        and not conversation.get("follow_up_enabled")
    )


def should_disable_follow_up_from_client_reply_fallback(conversation: Optional[dict], text_value: Optional[str]) -> bool:
    if not FOLLOW_UPS_ENABLED:
        return False
    if not conversation:
        return False
    normalized = " ".join((text_value or "").strip().lower().split())
    if not normalized:
        return False
    if not any(phrase in normalized for phrase in FOLLOW_UP_STOP_PHRASES):
        return False
    return bool(conversation.get("follow_up_sent_at") or conversation.get("default_follow_up_last_sent_at"))


def map_conversation_language(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized.startswith("ru"):
        return "RU"
    if normalized.startswith("en"):
        return "EN"
    return "UZ"


def build_crm_chat_url(conversation_id: int) -> str:
    return f"/cognilabsai/chat?conversation_id={conversation_id}"


def build_public_chat_url(conversation_id: int, frontend_base_url: Optional[str]) -> Optional[str]:
    base_url = (frontend_base_url or "").strip()
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}{build_crm_chat_url(conversation_id)}"


def build_chat_mode(channel: Optional[str]) -> tuple[str, bool]:
    normalized = (channel or "").strip().lower()
    if normalized == "telegram":
        return "telegram_operator", False
    if normalized == "instagram":
        return "instagram_ai", True
    if normalized == "website_ai":
        return "website_ai", True
    return f"{normalized or 'unknown'}_operator", False


def build_client_display_name(payload: dict) -> str:
    full_name = (payload.get("client_full_name") or "").strip()
    username = (payload.get("client_username") or "").strip()
    external_id = (payload.get("client_external_id") or "").strip()
    if full_name and username:
        return f"{full_name} (@{username.lstrip('@')})"
    if full_name:
        return full_name
    if username:
        return f"@{username.lstrip('@')}"
    channel = (payload.get("channel") or "").strip().lower()
    if channel == "instagram":
        return f"Instagram {external_id}" if external_id else "Instagram chat"
    if channel == "telegram":
        return f"Telegram {external_id}" if external_id else "Telegram chat"
    if channel == "website_ai":
        return full_name or "Website visitor"
    return external_id or "Unknown chat"


def is_website_discount_question(text: Optional[str]) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    keywords = [
        "chegirma",
        "скидк",
        "discount",
        "10%",
        "10 foiz",
        "10%",
        "aksiya",
        "акция",
        "promo",
    ]
    return any(keyword in normalized for keyword in keywords)


def extract_client_name_from_text(text: str) -> Optional[str]:
    value = (text or "").strip()
    if not value:
        return None
    patterns = [
        r"(?:^|[\s,.:;!?])ismim\s+([A-Za-zА-Яа-яЁёʻ’'`-]{2,}(?:\s+[A-Za-zА-Яа-яЁёʻ’'`-]{2,}){0,2})",
        r"(?:^|[\s,.:;!?])men(?:ing)?\s+ismim\s+([A-Za-zА-Яа-яЁёʻ’'`-]{2,}(?:\s+[A-Za-zА-Яа-яЁёʻ’'`-]{2,}){0,2})",
        r"(?:^|[\s,.:;!?])men\s+([A-Za-zА-Яа-яЁёʻ’'`-]{2,}(?:\s+[A-Za-zА-Яа-яЁёʻ’'`-]{2,}){0,2})",
        r"(?:^|[\s,.:;!?])меня\s+зовут\s+([A-Za-zА-Яа-яЁёʻ’'`-]{2,}(?:\s+[A-Za-zА-Яа-яЁёʻ’'`-]{2,}){0,2})",
        r"(?:^|[\s,.:;!?])my\s+name\s+is\s+([A-Za-zА-Яа-яЁёʻ’'`-]{2,}(?:\s+[A-Za-zА-Яа-яЁёʻ’'`-]{2,}){0,2})",
        r"(?:^|[\s,.:;!?])i[' ]?m\s+([A-Za-zА-Яа-яЁёʻ’'`-]{2,}(?:\s+[A-Za-zА-Яа-яЁёʻ’'`-]{2,}){0,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = " ".join(part for part in match.group(1).strip().split() if part)
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in {"kerak", "ha", "yoq", "yo'q", "salom"}:
            continue
        return candidate[:255]
    return None


def is_name_request_text(text: str) -> bool:
    value = (text or "").strip().lower()
    if not value:
        return False
    patterns = [
        "ismingizni",
        "ismimni",
        "ismim",
        "ismingiz",
        "исмингизни",
        "исмингиз",
        "исмингизни ҳам",
        "как вас зовут",
        "ваше имя",
        "вашe имя",
        "name",
        "your name",
    ]
    return any(pattern in value for pattern in patterns)


def extract_name_from_name_reply(text: str) -> Optional[str]:
    value = " ".join((text or "").strip().split())
    if not value:
        return None
    if len(value) > 60:
        return None
    if any(ch.isdigit() for ch in value):
        return None
    if "\n" in text:
        return None
    cleaned = re.sub(r"^[^A-Za-zА-Яа-яЁёʻ’'`-]+|[^A-Za-zА-Яа-яЁёʻ’'`-]+$", "", value)
    if not cleaned:
        return None
    parts = [part for part in cleaned.split() if part]
    if not 1 <= len(parts) <= 3:
        return None
    if any(len(part) < 2 for part in parts):
        return None
    lowered = cleaned.lower()
    blocked = {
        "ha", "yoq", "yo'q", "rahmat", "salom", "assalomu alaykum",
        "hozir", "ertaga", "bugun", "кейин", "сейчас", "завтра",
    }
    if lowered in blocked:
        return None
    return cleaned[:255]


async def infer_conversation_client_name(session: AsyncSession, conversation_id: int) -> Optional[str]:
    result = await session.execute(
        select(cognilabsai_message.c.sender_type, cognilabsai_message.c.text)
        .where(
            cognilabsai_message.c.conversation_id == conversation_id,
        )
        .order_by(cognilabsai_message.c.created_at.asc(), cognilabsai_message.c.id.asc())
    )
    previous_ai_text = None
    for row in result.mappings().all():
        sender_type = row["sender_type"]
        text_value = row["text"] or ""
        if sender_type == "ai":
            previous_ai_text = text_value
            continue
        if sender_type != "client":
            continue
        inferred_name = extract_client_name_from_text(text_value or "")
        if inferred_name:
            return inferred_name
        if previous_ai_text and is_name_request_text(previous_ai_text):
            inferred_name = extract_name_from_name_reply(text_value)
            if inferred_name:
                return inferred_name
    return None


async def backfill_instagram_client_names(session: AsyncSession) -> int:
    result = await session.execute(
        select(cognilabsai_conversation.c.id, cognilabsai_conversation.c.lead_full_name)
        .where(
            cognilabsai_conversation.c.channel == "instagram",
            cognilabsai_conversation.c.client_full_name.is_(None),
        )
    )
    updated_count = 0
    for row in result.mappings().all():
        inferred_name = (row.get("lead_full_name") or "").strip() or await infer_conversation_client_name(session, row["id"])
        if not inferred_name:
            continue
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == row["id"])
            .values(
                client_full_name=inferred_name[:255],
                updated_at=utcnow(),
            )
        )
        updated_count += 1
    if updated_count:
        await session.commit()
    return updated_count


def decorate_conversation_payload(payload: dict) -> dict:
    chat_mode, supports_ai = build_chat_mode(payload.get("channel"))
    enriched = dict(payload)
    enriched["chat_mode"] = chat_mode
    enriched["supports_ai"] = supports_ai
    enriched["client_display_name"] = build_client_display_name(payload)
    enriched["unread_count"] = int(enriched.get("unread_count") or 0)
    enriched["follow_up_enabled"] = bool(enriched.get("follow_up_enabled"))
    enriched.setdefault("telegram_is_online", None)
    enriched.setdefault("telegram_presence_status", None)
    enriched.setdefault("telegram_last_seen_at", None)
    return enriched


def normalize_telegram_search_query(value: str) -> str:
    normalized = (value or "").strip()
    if normalized.startswith("https://t.me/"):
        normalized = normalized.rsplit("/", 1)[-1]
    elif normalized.startswith("http://t.me/"):
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized.lstrip("@").strip().lower()


def build_telegram_search_rank(item: dict, normalized_query: str) -> tuple[int, int, int, str]:
    username = (item.get("username") or "").strip().lower()
    peer = (item.get("peer") or "").strip().lower()
    full_name = (item.get("full_name") or "").strip().lower()
    exact_username = username == normalized_query
    exact_peer = peer == normalized_query
    starts_username = bool(username) and username.startswith(normalized_query)
    starts_peer = bool(peer) and peer.startswith(normalized_query)
    contains_name = bool(full_name) and normalized_query in full_name
    has_existing = item.get("existing_conversation_id") is not None
    has_avatar = bool(item.get("avatar_url"))
    if exact_username or exact_peer:
        match_bucket = 0
    elif starts_username or starts_peer:
        match_bucket = 1
    elif contains_name:
        match_bucket = 2
    else:
        match_bucket = 3
    return (
        match_bucket,
        0 if has_existing else 1,
        0 if has_avatar else 1,
        username or peer or full_name,
    )


async def ensure_schema(session: AsyncSession):
    global schema_ready
    if schema_ready:
        return
    async with schema_lock:
        if schema_ready:
            return
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
            cognilabs_telegram_token TEXT,
            cognilabs_channel_id VARCHAR(255),
            frontend_base_url VARCHAR(1000),
            instagram_followup_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            instagram_followup_delay_minutes INTEGER NULL,
            instagram_followup_message TEXT NULL,
            telegram_followup_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            telegram_followup_delay_minutes INTEGER NULL,
            telegram_followup_message TEXT NULL,
            instagram_default_followup_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            instagram_default_followup_step1_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            instagram_default_followup_step1_delay_minutes INTEGER NULL,
            instagram_default_followup_step1_message TEXT NULL,
            instagram_default_followup_step2_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            instagram_default_followup_step2_delay_minutes INTEGER NULL,
            instagram_default_followup_step2_message TEXT NULL,
            instagram_default_followup_step3_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            instagram_default_followup_step3_delay_minutes INTEGER NULL,
            instagram_default_followup_step3_message TEXT NULL,
            ai_globally_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            ai_enabled_since TIMESTAMP NULL,
            websocket_api_key VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS cognilabs_telegram_token TEXT
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS cognilabs_channel_id VARCHAR(255)
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS frontend_base_url VARCHAR(1000)
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_followup_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_followup_delay_minutes INTEGER NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_followup_message TEXT NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS telegram_followup_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS telegram_followup_delay_minutes INTEGER NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS telegram_followup_message TEXT NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_enabled BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step1_enabled BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step1_delay_minutes INTEGER NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step1_message TEXT NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step2_enabled BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step2_delay_minutes INTEGER NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step2_message TEXT NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step3_enabled BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step3_delay_minutes INTEGER NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS instagram_default_followup_step3_message TEXT NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS ai_globally_enabled BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_global_integration
            ADD COLUMN IF NOT EXISTS ai_enabled_since TIMESTAMP NULL
        """))
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS cognilabsai_conversation (
            id SERIAL PRIMARY KEY,
            channel VARCHAR(32) NOT NULL,
            client_external_id VARCHAR(255) NOT NULL,
            client_username VARCHAR(255),
            client_full_name VARCHAR(255),
            client_avatar_url VARCHAR(1000),
            instagram_business_id VARCHAR(255),
            ai_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            lead_created BOOLEAN NOT NULL DEFAULT FALSE,
            crm_customer_id INTEGER NULL,
            lead_full_name VARCHAR(255) NULL,
            lead_phone_number VARCHAR(64) NULL,
            lead_business_field VARCHAR(255) NULL,
            lead_scheduled_time VARCHAR(255) NULL,
            last_lead_created_at TIMESTAMP NULL,
            unread_count INTEGER NOT NULL DEFAULT 0,
            pause_reason VARCHAR(64),
            paused_until TIMESTAMP NULL,
            follow_up_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            follow_up_mode VARCHAR(32) NULL,
            follow_up_delay_minutes INTEGER NULL,
            follow_up_message TEXT NULL,
            follow_up_due_at TIMESTAMP NULL,
            follow_up_sent_at TIMESTAMP NULL,
            default_follow_up_last_step INTEGER NOT NULL DEFAULT 0,
            default_follow_up_due_at TIMESTAMP NULL,
            default_follow_up_last_sent_at TIMESTAMP NULL,
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
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS client_avatar_url VARCHAR(1000) NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS lead_created BOOLEAN NOT NULL DEFAULT FALSE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS crm_customer_id INTEGER NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS lead_full_name VARCHAR(255) NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS lead_phone_number VARCHAR(64) NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS lead_business_field VARCHAR(255) NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS lead_scheduled_time VARCHAR(255) NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS last_lead_created_at TIMESTAMP NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS unread_count INTEGER NOT NULL DEFAULT 0
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS follow_up_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS follow_up_mode VARCHAR(32) NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS follow_up_delay_minutes INTEGER NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS follow_up_message TEXT NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS follow_up_due_at TIMESTAMP NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS follow_up_sent_at TIMESTAMP NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS default_follow_up_last_step INTEGER NOT NULL DEFAULT 0
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS default_follow_up_due_at TIMESTAMP NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_conversation
            ADD COLUMN IF NOT EXISTS default_follow_up_last_sent_at TIMESTAMP NULL
        """))
        await session.execute(text("""
            UPDATE cognilabsai_conversation
            SET last_lead_created_at = updated_at
            WHERE lead_created = TRUE
              AND last_lead_created_at IS NULL
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
            media_type VARCHAR(64) NULL,
            media_url VARCHAR(1000) NULL,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            read_at TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_message
            ADD COLUMN IF NOT EXISTS is_read BOOLEAN NOT NULL DEFAULT FALSE
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_message
            ADD COLUMN IF NOT EXISTS read_at TIMESTAMP NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_message
            ADD COLUMN IF NOT EXISTS media_type VARCHAR(64) NULL
        """))
        await session.execute(text("""
            ALTER TABLE cognilabsai_message
            ADD COLUMN IF NOT EXISTS media_url VARCHAR(1000) NULL
        """))
        await session.execute(text("""
            UPDATE cognilabsai_message
            SET is_read = TRUE, read_at = COALESCE(read_at, created_at)
            WHERE sender_type IN ('ai', 'operator', 'system')
              AND is_read = FALSE
        """))
        await session.execute(text("""
            UPDATE cognilabsai_conversation c
            SET unread_count = COALESCE(sub.unread_count, 0)
            FROM (
                SELECT conversation_id, COUNT(*)::INTEGER AS unread_count
                FROM cognilabsai_message
                WHERE sender_type = 'client' AND is_read = FALSE
                GROUP BY conversation_id
            ) AS sub
            WHERE c.id = sub.conversation_id
        """))
        await session.execute(text("""
            UPDATE cognilabsai_conversation
            SET unread_count = 0
            WHERE id NOT IN (
                SELECT DISTINCT conversation_id
                FROM cognilabsai_message
                WHERE sender_type = 'client' AND is_read = FALSE
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
        schema_ready = True


async def ensure_permission_pages(session: AsyncSession):
    await ensure_app_page_schema(session)
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
    result = await session.execute(
        select(cognilabsai_global_integration.c.id)
        .where(cognilabsai_global_integration.c.id == 1)
    )
    if result.scalar() is None:
        await session.execute(
            insert(cognilabsai_global_integration).values(
                id=1,
                openai_model=DEFAULT_OPENAI_MODEL,
                openai_base_url=DEFAULT_OPENAI_BASE_URL,
                instagram_verify_token=DEFAULT_VERIFY_TOKEN,
                instagram_followup_enabled=False,
                telegram_followup_enabled=False,
                instagram_default_followup_enabled=True,
                instagram_default_followup_step1_enabled=True,
                instagram_default_followup_step1_delay_minutes=DEFAULT_INSTAGRAM_FOLLOWUP_STEP1_DELAY_MINUTES,
                instagram_default_followup_step1_message=DEFAULT_INSTAGRAM_FOLLOWUP_STEP1_MESSAGE,
                instagram_default_followup_step2_enabled=True,
                instagram_default_followup_step2_delay_minutes=DEFAULT_INSTAGRAM_FOLLOWUP_STEP2_DELAY_MINUTES,
                instagram_default_followup_step2_message=DEFAULT_INSTAGRAM_FOLLOWUP_STEP2_MESSAGE,
                instagram_default_followup_step3_enabled=True,
                instagram_default_followup_step3_delay_minutes=DEFAULT_INSTAGRAM_FOLLOWUP_STEP3_DELAY_MINUTES,
                instagram_default_followup_step3_message=DEFAULT_INSTAGRAM_FOLLOWUP_STEP3_MESSAGE,
                ai_globally_enabled=True,
                ai_enabled_since=None,
                websocket_api_key=DEFAULT_WS_KEY,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )


def build_env_integration_defaults() -> dict:
    values = {}
    if OPENAI_API_KEY:
        values["openai_api_key"] = OPENAI_API_KEY
    if OPENAI_MODEL:
        values["openai_model"] = OPENAI_MODEL
    if OPENAI_BASE_URL:
        values["openai_base_url"] = OPENAI_BASE_URL
    if INSTAGRAM_BUSINESS_ID:
        values["instagram_business_id"] = INSTAGRAM_BUSINESS_ID
    if INSTAGRAM_ACCESS_TOKEN:
        values["instagram_access_token"] = INSTAGRAM_ACCESS_TOKEN
    if INSTAGRAM_VERIFY_TOKEN:
        values["instagram_verify_token"] = INSTAGRAM_VERIFY_TOKEN
    if TELEGRAM_API_ID:
        values["telegram_api_id"] = TELEGRAM_API_ID
    if TELEGRAM_API_HASH:
        values["telegram_api_hash"] = TELEGRAM_API_HASH
    if TELEGRAM_SESSION:
        values["telegram_session"] = TELEGRAM_SESSION
    if COGNILABS_TELEGRAM_TOKEN:
        values["cognilabs_telegram_token"] = COGNILABS_TELEGRAM_TOKEN
    if COGNILABS_CHANNEL_ID:
        values["cognilabs_channel_id"] = COGNILABS_CHANNEL_ID
    return values


async def get_integration_config(session: AsyncSession) -> dict:
    await ensure_schema(session)
    result = await session.execute(
        select(cognilabsai_global_integration)
        .where(cognilabsai_global_integration.c.id == 1)
    )
    row = result.mappings().first()
    config = dict(row)
    missing_updates = {
        key: value
        for key, value in build_env_integration_defaults().items()
        if not config.get(key)
    }
    if missing_updates:
        missing_updates["updated_at"] = utcnow()
        await session.execute(
            update(cognilabsai_global_integration)
            .where(cognilabsai_global_integration.c.id == 1)
            .values(**missing_updates)
        )
        await session.commit()
        config.update(missing_updates)
    return config


def apply_global_ai_toggle_to_payload(current_config: dict, payload: dict) -> dict:
    if "ai_enabled_since" in payload:
        payload.pop("ai_enabled_since", None)
    if "ai_globally_enabled" not in payload:
        return payload
    enabled = bool(payload.get("ai_globally_enabled"))
    current_enabled = bool(current_config.get("ai_globally_enabled", True))
    if enabled and not current_enabled:
        payload["ai_enabled_since"] = utcnow()
    elif not enabled:
        payload["ai_enabled_since"] = None
    return payload


async def update_integration_config(session: AsyncSession, payload: dict) -> dict:
    await ensure_schema(session)
    current_config = await get_integration_config(session)
    payload = apply_global_ai_toggle_to_payload(current_config, payload)
    payload["updated_at"] = utcnow()
    await session.execute(
        update(cognilabsai_global_integration)
        .where(cognilabsai_global_integration.c.id == 1)
        .values(**payload)
    )
    await session.commit()
    await refresh_global_follow_up_schedules(session)
    await refresh_default_instagram_follow_up_schedules(session)
    await telegram_userbot_manager.restart()
    return await get_integration_config(session)


async def update_global_ai_state(session: AsyncSession, enabled: bool) -> dict:
    return await update_integration_config(
        session,
        {
            "ai_globally_enabled": bool(enabled),
        },
    )


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
    items = [decorate_conversation_payload(dict(row)) for row in result.mappings().all()]
    await session.rollback()
    for item in items:
        if item.get("channel") != "telegram":
            continue
        try:
            snapshot = await telegram_userbot_manager.resolve_peer_snapshot(item["client_external_id"])
            item["telegram_is_online"] = snapshot.get("is_online")
            item["telegram_presence_status"] = snapshot.get("presence_status")
            item["telegram_last_seen_at"] = snapshot.get("last_seen_at")
        except Exception:
            pass
    return items


async def get_conversation(session: AsyncSession, conversation_id: int) -> Optional[dict]:
    await ensure_schema(session)
    result = await session.execute(
        select(cognilabsai_conversation).where(cognilabsai_conversation.c.id == conversation_id)
    )
    row = result.mappings().first()
    if not row:
        return None
    conversation = decorate_conversation_payload(dict(row))
    await session.rollback()
    if conversation.get("channel") == "telegram":
        try:
            snapshot = await telegram_userbot_manager.resolve_peer_snapshot(conversation["client_external_id"])
            conversation["telegram_is_online"] = snapshot.get("is_online")
            conversation["telegram_presence_status"] = snapshot.get("presence_status")
            conversation["telegram_last_seen_at"] = snapshot.get("last_seen_at")
        except Exception:
            pass
    return conversation


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


async def get_latest_client_message_text(session: AsyncSession, conversation_id: int) -> Optional[str]:
    result = await session.execute(
        select(cognilabsai_message.c.text)
        .where(
            cognilabsai_message.c.conversation_id == conversation_id,
            cognilabsai_message.c.sender_type == "client",
        )
        .order_by(cognilabsai_message.c.created_at.desc(), cognilabsai_message.c.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_client_message_created_at(session: AsyncSession, conversation_id: int) -> Optional[datetime]:
    result = await session.execute(
        select(cognilabsai_message.c.created_at)
        .where(
            cognilabsai_message.c.conversation_id == conversation_id,
            cognilabsai_message.c.sender_type == "client",
        )
        .order_by(cognilabsai_message.c.created_at.desc(), cognilabsai_message.c.id.desc())
        .limit(1)
    )
    return normalize_datetime(result.scalar_one_or_none())


async def should_disable_follow_up_from_client_reply(
    session: AsyncSession,
    conversation: Optional[dict],
    text_value: Optional[str],
) -> bool:
    if not FOLLOW_UPS_ENABLED:
        return False
    if not conversation:
        return False
    if not (conversation.get("follow_up_sent_at") or conversation.get("default_follow_up_last_sent_at")):
        return False
    normalized = (text_value or "").strip()
    if not normalized:
        return False
    config = await get_integration_config(session)
    api_key = (config.get("openai_api_key") or "").strip()
    if not api_key:
        return should_disable_follow_up_from_client_reply_fallback(conversation, normalized)
    base_url = (config.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    model = config.get("openai_model") or DEFAULT_OPENAI_MODEL
    recent_messages = await get_messages(session, conversation["id"], limit=6, offset=0)
    recent_lines: list[str] = []
    for item in recent_messages[-6:]:
        sender_type = item.get("sender_type")
        if sender_type == "client":
            role = "Client"
        elif sender_type == "system":
            role = "Follow-up"
        elif sender_type == "ai":
            role = "AI"
        else:
            role = "Operator"
        recent_lines.append(f"{role}: {item.get('text', '')}")
    classification_messages = [
        {
            "role": "system",
            "content": (
                "You classify whether future follow-up messages should be stopped for this client. "
                "Return only STOP or CONTINUE. "
                "Return STOP if the client's latest message means no need, thanks that's enough, not interested, "
                "do not write/call, complaint, dissatisfaction, or any clear signal that further follow-up should stop. "
                "Return CONTINUE if the client is still interested, asking a question, wants more details, or the intent is unclear."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Conversation channel: {conversation.get('channel')}\n"
                f"Recent conversation:\n{chr(10).join(recent_lines)}\n\n"
                f"Latest client message:\n{normalized}"
            ),
        },
    ]
    payload = apply_reasoning_defaults({
        "model": model,
        "messages": classification_messages,
        "max_completion_tokens": 32,
        "temperature": 0,
    }, model)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
            if response.status_code >= 400:
                print(f"Follow-up classifier error {response.status_code}: {response.text}", flush=True)
                return should_disable_follow_up_from_client_reply_fallback(conversation, normalized)
            data = response.json()
    except Exception as exc:
        print(f"Follow-up classifier request error: {exc}", flush=True)
        return should_disable_follow_up_from_client_reply_fallback(conversation, normalized)
    choices = data.get("choices") or []
    if not choices:
        return should_disable_follow_up_from_client_reply_fallback(conversation, normalized)
    content = extract_chat_message_text(choices[0].get("message") or {}).upper()
    if "STOP" in content:
        return True
    if "CONTINUE" in content:
        return False
    return should_disable_follow_up_from_client_reply_fallback(conversation, normalized)


async def get_conversation_record(session: AsyncSession, conversation_id: int) -> Optional[dict]:
    result = await session.execute(
        select(cognilabsai_conversation).where(cognilabsai_conversation.c.id == conversation_id)
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def recalculate_follow_up_schedule(session: AsyncSession, conversation_id: int, base_time: Optional[datetime] = None) -> Optional[dict]:
    conversation = await get_conversation_record(session, conversation_id)
    if not conversation:
        return None
    if not FOLLOW_UPS_ENABLED:
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == conversation_id)
            .values(
                follow_up_enabled=False,
                follow_up_mode=None,
                follow_up_delay_minutes=None,
                follow_up_message=None,
                follow_up_due_at=None,
                follow_up_sent_at=None,
                updated_at=utcnow(),
            )
        )
        await session.commit()
        return await get_conversation(session, conversation_id)
    config = await get_integration_config(session)
    enabled, delay_minutes, message = get_conversation_follow_up_settings(conversation, config)
    values = {
        "updated_at": utcnow(),
        "follow_up_sent_at": None,
    }
    if enabled and delay_minutes and message:
        anchor_time = normalize_datetime(base_time) or normalize_datetime(conversation.get("last_message_at")) or utcnow()
        values["follow_up_due_at"] = anchor_time + timedelta(minutes=int(delay_minutes))
    else:
        values["follow_up_due_at"] = None
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(**values)
    )
    await session.commit()
    return await get_conversation(session, conversation_id)


async def recalculate_default_instagram_follow_up_schedule(
    session: AsyncSession,
    conversation_id: int,
    base_time: Optional[datetime] = None,
    preserve_progress: bool = False,
) -> Optional[dict]:
    conversation = await get_conversation_record(session, conversation_id)
    if not conversation:
        return None
    if not FOLLOW_UPS_ENABLED:
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == conversation_id)
            .values(
                default_follow_up_last_step=0,
                default_follow_up_due_at=None,
                default_follow_up_last_sent_at=None,
                updated_at=utcnow(),
            )
        )
        await session.commit()
        return await get_conversation(session, conversation_id)
    config = await get_integration_config(session)
    values = {"updated_at": utcnow()}
    if not is_default_instagram_follow_up_eligible(conversation) or not is_default_instagram_follow_up_globally_enabled(config):
        if not preserve_progress:
            values["default_follow_up_last_step"] = 0
            values["default_follow_up_last_sent_at"] = None
        values["default_follow_up_due_at"] = None
    else:
        steps = get_default_instagram_follow_up_steps(config)
        if not preserve_progress:
            values["default_follow_up_last_step"] = 0
            values["default_follow_up_last_sent_at"] = None
            anchor_time = normalize_datetime(base_time) or normalize_datetime(conversation.get("last_message_at")) or utcnow()
            values["default_follow_up_due_at"] = anchor_time + timedelta(minutes=steps[0][1]) if steps else None
        else:
            current_step = int(conversation.get("default_follow_up_last_step") or 0)
            current_index = -1
            for index, (step_number, _, _) in enumerate(steps):
                if step_number == current_step:
                    current_index = index
                    break
            if current_step and current_index == -1:
                values["default_follow_up_due_at"] = None
            elif current_index + 1 >= len(steps):
                values["default_follow_up_due_at"] = None
            else:
                anchor_time = normalize_datetime(base_time) or normalize_datetime(conversation.get("last_message_at")) or utcnow()
                values["default_follow_up_due_at"] = anchor_time + timedelta(minutes=steps[current_index + 1][1])
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(**values)
    )
    await session.commit()
    return await get_conversation(session, conversation_id)


async def refresh_global_follow_up_schedules(session: AsyncSession) -> None:
    if not FOLLOW_UPS_ENABLED:
        await session.execute(
            update(cognilabsai_conversation).values(
                follow_up_enabled=False,
                follow_up_mode=None,
                follow_up_delay_minutes=None,
                follow_up_message=None,
                follow_up_due_at=None,
                follow_up_sent_at=None,
                updated_at=utcnow(),
            )
        )
        await session.commit()
        return
    result = await session.execute(
        select(cognilabsai_conversation.c.id)
        .where(
            cognilabsai_conversation.c.follow_up_enabled == True,
            cognilabsai_conversation.c.follow_up_mode == "global",
        )
    )
    for row in result.all():
        await recalculate_follow_up_schedule(session, row[0])


async def refresh_default_instagram_follow_up_schedules(session: AsyncSession) -> None:
    if not FOLLOW_UPS_ENABLED:
        await session.execute(
            update(cognilabsai_conversation).values(
                default_follow_up_last_step=0,
                default_follow_up_due_at=None,
                default_follow_up_last_sent_at=None,
                updated_at=utcnow(),
            )
        )
        await session.commit()
        return
    result = await session.execute(
        select(cognilabsai_conversation.c.id)
        .where(
            cognilabsai_conversation.c.channel == "instagram",
            cognilabsai_conversation.c.crm_customer_id.is_(None),
            cognilabsai_conversation.c.follow_up_enabled == False,
        )
    )
    for row in result.all():
        await recalculate_default_instagram_follow_up_schedule(session, row[0], preserve_progress=True)


async def update_conversation_follow_up(session: AsyncSession, conversation_id: int, payload: dict) -> Optional[dict]:
    conversation = await get_conversation_record(session, conversation_id)
    if not conversation:
        return None
    if not FOLLOW_UPS_ENABLED:
        values = {
            "follow_up_enabled": False,
            "follow_up_mode": None,
            "follow_up_delay_minutes": None,
            "follow_up_message": None,
            "follow_up_due_at": None,
            "follow_up_sent_at": None,
            "default_follow_up_last_step": 0,
            "default_follow_up_due_at": None,
            "default_follow_up_last_sent_at": None,
            "updated_at": utcnow(),
        }
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == conversation_id)
            .values(**values)
        )
        await session.commit()
        updated = await get_conversation(session, conversation_id)
        if updated:
            await manager.broadcast(
                {
                    "type": "conversation.updated",
                    "conversation": updated,
                },
                conversation_id=conversation_id,
            )
        return updated
    enabled = bool(payload.get("enabled"))
    mode = (payload.get("mode") or "").strip().lower() or None
    if enabled and mode not in {"global", "custom"}:
        raise ValueError("mode must be global or custom")
    custom_delay_minutes = None
    custom_message = None
    if enabled and mode == "custom":
        custom_delay_minutes = payload.get("delay_minutes")
        custom_message = (payload.get("message") or "").strip() or None
        if not custom_delay_minutes or custom_delay_minutes <= 0 or not custom_message:
            raise ValueError("custom follow-up requires delay_minutes and message")
    values = {
        "follow_up_enabled": enabled,
        "follow_up_mode": mode if enabled else None,
        "follow_up_delay_minutes": custom_delay_minutes if enabled and mode == "custom" else None,
        "follow_up_message": custom_message if enabled and mode == "custom" else None,
        "follow_up_due_at": None,
        "follow_up_sent_at": None,
        "default_follow_up_last_step": 0,
        "default_follow_up_due_at": None,
        "default_follow_up_last_sent_at": None,
        "updated_at": utcnow(),
    }
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(**values)
    )
    await session.commit()
    if not enabled:
        await recalculate_default_instagram_follow_up_schedule(session, conversation_id)
    updated = await recalculate_follow_up_schedule(session, conversation_id)
    if updated:
        await manager.broadcast(
            {
                "type": "conversation.updated",
                "conversation": updated,
            },
            conversation_id=conversation_id,
        )
    return updated


async def delete_conversation(session: AsyncSession, conversation_id: int) -> bool:
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        return False
    await session.execute(
        update(cognilabsai_import_log)
        .where(cognilabsai_import_log.c.conversation_id == conversation_id)
        .values(conversation_id=None)
    )
    await session.execute(
        delete(cognilabsai_pause_event)
        .where(cognilabsai_pause_event.c.conversation_id == conversation_id)
    )
    await session.execute(
        delete(cognilabsai_message)
        .where(cognilabsai_message.c.conversation_id == conversation_id)
    )
    await session.execute(
        delete(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
    )
    await session.commit()
    await manager.broadcast(
        {
            "type": "conversation.deleted",
            "conversation_id": conversation_id,
        },
        conversation_id=conversation_id,
    )
    return True


async def mark_conversation_read(session: AsyncSession, conversation_id: int, sync_remote: bool = True) -> Optional[dict]:
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        return None
    now = utcnow()
    await session.execute(
        update(cognilabsai_message)
        .where(
            cognilabsai_message.c.conversation_id == conversation_id,
            cognilabsai_message.c.sender_type == "client",
            cognilabsai_message.c.is_read == False,
        )
        .values(
            is_read=True,
            read_at=now,
        )
    )
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(
            unread_count=0,
            updated_at=now,
        )
    )
    await session.commit()
    if sync_remote and conversation.get("channel") == "telegram":
        try:
            await asyncio.wait_for(
                telegram_userbot_manager.mark_read(conversation["client_external_id"]),
                timeout=5,
            )
        except Exception as exc:
            print(f"Telegram mark read error for conversation {conversation_id}: {exc}", flush=True)
    updated = await get_conversation(session, conversation_id)
    if updated:
        await manager.broadcast(
            {
                "type": "conversation.updated",
                "conversation": updated,
            },
            conversation_id=conversation_id,
        )
    return updated


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
    client_avatar_url: Optional[str] = None,
    instagram_business_id: Optional[str] = None,
    is_imported: bool = False,
) -> dict:
    await ensure_schema(session)
    now = utcnow()
    insert_stmt = pg_insert(cognilabsai_conversation).values(
        channel=channel,
        client_external_id=client_external_id,
        client_username=client_username,
        client_full_name=client_full_name,
        client_avatar_url=client_avatar_url,
        instagram_business_id=instagram_business_id,
        ai_enabled=True,
        lead_created=False,
        lead_full_name=None,
        lead_phone_number=None,
        lead_business_field=None,
        lead_scheduled_time=None,
        last_lead_created_at=None,
        follow_up_enabled=False,
        follow_up_mode=None,
        follow_up_delay_minutes=None,
        follow_up_message=None,
        follow_up_due_at=None,
        follow_up_sent_at=None,
        default_follow_up_last_step=0,
        default_follow_up_due_at=None,
        default_follow_up_last_sent_at=None,
        pause_reason=None,
        paused_until=None,
        last_message_at=None,
        last_message_preview=None,
        last_operator_user_id=None,
        last_operator_name=None,
        is_imported=is_imported,
        created_at=now,
        updated_at=now,
    )
    updates = {
        "updated_at": now,
    }
    if client_username:
        updates["client_username"] = insert_stmt.excluded.client_username
    if client_full_name:
        updates["client_full_name"] = insert_stmt.excluded.client_full_name
    if client_avatar_url:
        updates["client_avatar_url"] = insert_stmt.excluded.client_avatar_url
    if instagram_business_id:
        updates["instagram_business_id"] = insert_stmt.excluded.instagram_business_id
    if is_imported:
        updates["is_imported"] = True
    insert_result = await session.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=[
                cognilabsai_conversation.c.channel,
                cognilabsai_conversation.c.client_external_id,
            ],
            set_=updates,
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


def generate_website_session_id() -> str:
    return uuid4().hex


async def get_conversation_by_channel_and_client_external_id(
    session: AsyncSession,
    *,
    channel: str,
    client_external_id: str,
) -> Optional[dict]:
    await ensure_schema(session)
    result = await session.execute(
        select(cognilabsai_conversation).where(
            cognilabsai_conversation.c.channel == channel,
            cognilabsai_conversation.c.client_external_id == client_external_id,
        )
    )
    row = result.mappings().first()
    return decorate_conversation_payload(dict(row)) if row else None


def build_virtual_website_conversation(session_id: str) -> dict:
    return decorate_conversation_payload(
        {
            "id": 0,
            "channel": "website_ai",
            "client_external_id": session_id,
            "client_username": None,
            "client_full_name": None,
            "client_avatar_url": None,
            "instagram_business_id": None,
            "unread_count": 0,
            "ai_enabled": True,
            "pause_reason": None,
            "paused_until": None,
            "follow_up_enabled": False,
            "follow_up_mode": None,
            "follow_up_delay_minutes": None,
            "follow_up_message": None,
            "follow_up_due_at": None,
            "follow_up_sent_at": None,
            "default_follow_up_last_step": 0,
            "default_follow_up_due_at": None,
            "default_follow_up_last_sent_at": None,
            "last_message_at": None,
            "last_message_preview": None,
            "last_operator_user_id": None,
            "last_operator_name": None,
            "is_imported": False,
            "created_at": None,
            "updated_at": None,
        }
    )


async def init_website_session(session: AsyncSession, session_id: Optional[str]) -> dict:
    normalized_session_id = (session_id or "").strip() or generate_website_session_id()
    conversation = await get_conversation_by_channel_and_client_external_id(
        session,
        channel="website_ai",
        client_external_id=normalized_session_id,
    )
    if conversation:
        messages = await get_messages(session, conversation["id"], limit=500, offset=0)
    else:
        conversation = build_virtual_website_conversation(normalized_session_id)
        messages = []
    return {
        "session_id": normalized_session_id,
        "conversation": conversation,
        "messages": messages,
    }


async def send_website_message(session: AsyncSession, session_id: str, text_value: str) -> dict:
    normalized_session_id = (session_id or "").strip() or generate_website_session_id()
    conversation = await upsert_conversation(
        session,
        channel="website_ai",
        client_external_id=normalized_session_id,
    )
    await create_message(
        session,
        conversation_id=conversation["id"],
        channel="website_ai",
        sender_type="client",
        text_value=text_value,
        client_external_id=normalized_session_id,
    )
    try:
        await maybe_send_ai_reply(session, conversation["id"])
    except Exception as exc:
        print(f"Website AI reply error for conversation {conversation['id']}: {exc}", flush=True)
    return await init_website_session(session, normalized_session_id)


async def create_message(
    session: AsyncSession,
    *,
    conversation_id: int,
    channel: str,
    sender_type: str,
    text_value: str,
    media_type: Optional[str] = None,
    media_url: Optional[str] = None,
    operator_user_id: Optional[int] = None,
    operator_name_snapshot: Optional[str] = None,
    client_external_id: Optional[str] = None,
    instagram_message_id: Optional[str] = None,
    telegram_message_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> dict:
    ts = normalize_datetime(created_at) or utcnow()
    is_client_message = sender_type == "client"
    conversation_before = await get_conversation_record(session, conversation_id)
    disable_follow_ups = False
    if is_client_message:
        disable_follow_ups = await should_disable_follow_up_from_client_reply(session, conversation_before, text_value)
    previous_ai_text = None
    if channel == "instagram" and sender_type == "client":
        previous_result = await session.execute(
            select(cognilabsai_message.c.text)
            .where(
                cognilabsai_message.c.conversation_id == conversation_id,
                cognilabsai_message.c.sender_type == "ai",
            )
            .order_by(cognilabsai_message.c.created_at.desc(), cognilabsai_message.c.id.desc())
            .limit(1)
        )
        previous_ai_text = previous_result.scalar_one_or_none()
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
            media_type=media_type,
            media_url=media_url,
            is_read=not is_client_message,
            read_at=None if is_client_message else ts,
            created_at=ts,
        ).returning(cognilabsai_message.c.id)
    )
    message_id = result.scalar_one()
    conversation_updates = {
        "last_message_at": ts,
        "last_message_preview": text_value[:1000],
        "unread_count": (cognilabsai_conversation.c.unread_count + 1) if is_client_message else cognilabsai_conversation.c.unread_count,
        "updated_at": utcnow(),
    }
    if disable_follow_ups:
        conversation_updates.update(
            {
                "follow_up_enabled": False,
                "follow_up_mode": None,
                "follow_up_delay_minutes": None,
                "follow_up_message": None,
                "follow_up_due_at": None,
                "follow_up_sent_at": None,
                "default_follow_up_last_step": 0,
                "default_follow_up_due_at": None,
                "default_follow_up_last_sent_at": None,
            }
        )
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(**conversation_updates)
    )
    if channel == "instagram" and sender_type == "client":
        inferred_name = extract_client_name_from_text(text_value)
        if not inferred_name and previous_ai_text and is_name_request_text(previous_ai_text):
            inferred_name = extract_name_from_name_reply(text_value)
        if inferred_name:
            await session.execute(
                update(cognilabsai_conversation)
                .where(
                    cognilabsai_conversation.c.id == conversation_id,
                    cognilabsai_conversation.c.client_full_name.is_(None),
                )
                .values(
                    client_full_name=inferred_name,
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
    if sender_type != "system":
        if disable_follow_ups:
            updated_conversation = await get_conversation(session, conversation_id)
            if updated_conversation:
                await manager.broadcast(
                    {
                        "type": "conversation.updated",
                        "conversation": updated_conversation,
                    },
                    conversation_id=conversation_id,
                )
            return message
        default_updated_conversation = await recalculate_default_instagram_follow_up_schedule(session, conversation_id, base_time=ts)
        if default_updated_conversation:
            await manager.broadcast(
                {
                    "type": "conversation.updated",
                    "conversation": default_updated_conversation,
                },
                conversation_id=conversation_id,
            )
        updated_conversation = await recalculate_follow_up_schedule(session, conversation_id, base_time=ts)
        if updated_conversation:
            await manager.broadcast(
                {
                    "type": "conversation.updated",
                    "conversation": updated_conversation,
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
    url = "https://graph.instagram.com/v17.0/me/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "instagram",
        "recipient": {"id": recipient_id},
        "message": {"text": text_value},
        "metadata": "by_bot",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            error_code = None
            error_subcode = None
            error_message = response.text
            with contextlib.suppress(Exception):
                error_payload = response.json().get("error") or {}
                error_code = error_payload.get("code")
                error_subcode = error_payload.get("error_subcode")
                error_message = error_payload.get("message") or response.text
            print(
                f"Instagram send error status={response.status_code} code={error_code} subcode={error_subcode}: {error_message}",
                flush=True,
            )
            raise InstagramSendError(
                error_message,
                status_code=response.status_code,
                error_code=error_code,
                error_subcode=error_subcode,
            )
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
    elif conversation["channel"] == "website_ai":
        message = await create_message(
            session,
            conversation_id=conversation_id,
            channel="website_ai",
            sender_type="operator",
            text_value=text_value,
            operator_user_id=current_user.id,
            operator_name_snapshot=operator_name,
            client_external_id=conversation["client_external_id"],
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
    conversation = await get_conversation(session, conversation_id)
    history = await get_messages(session, conversation_id, limit=30, offset=0)
    prompt = config.get("system_prompt") or ""
    messages = []
    if prompt:
        messages.append({"role": "system", "content": prompt})
    messages.append({"role": "system", "content": COGNILABSAI_BEHAVIOR_PROMPT})
    if is_lead_cooldown_active(conversation):
        messages.append({
            "role": "system",
            "content": f"Lead was already created in the last {LEAD_COOLDOWN_HOURS} hours for this conversation. Do not call register_customer again and do not repeat confirmation.",
        })
    for item in history:
        role = "user"
        if item["sender_type"] == "ai":
            role = "assistant"
        elif item["sender_type"] == "operator":
            role = "assistant"
        messages.append({"role": role, "content": item["text"]})
    return messages


async def build_legacy_user_context(session: AsyncSession, conversation_id: int) -> str:
    history = await get_messages(session, conversation_id, limit=50, offset=0)
    lines: list[str] = []
    for item in history:
        if item["sender_type"] == "client":
            lines.append(f"User: {item['text']}")
        elif item["sender_type"] == "ai":
            lines.append(f"Assistant: {item['text']}")
        elif item["sender_type"] == "operator":
            lines.append(f"Operator: {item['text']}")
    return "\n".join(lines)


def is_missing_required_value(value: Optional[str]) -> bool:
    normalized = (value or "").strip().lower()
    invalid_values = {
        "", "-", "yoq", "yo'q", "bilmayman", "none", "null", "n/a", "na",
        "нет", "не знаю", "unknown",
    }
    return normalized in invalid_values


def normalize_uzbek_phone(phone_number: Optional[str]) -> str:
    raw = re.sub(r"\D+", "", phone_number or "")
    if raw.startswith("998") and len(raw) == 12:
        return f"+{raw}"
    if len(raw) == 9:
        return f"+998{raw}"
    if raw.startswith("0") and len(raw) == 10:
        return f"+998{raw[1:]}"
    return phone_number or ""


def sanitize_ai_reply_text(reply_text: Optional[str]) -> str:
    text_value = (reply_text or "").strip()
    if not text_value:
        return ""
    text_value = re.sub(
        r"^\s*(?:alisher|assistant|operator)\s*[:\-|]\s*",
        "",
        text_value,
        flags=re.IGNORECASE,
    ).strip()
    return text_value


def is_gpt5_model(model: Optional[str]) -> bool:
    return (model or "").strip().lower().startswith("gpt-5")


def apply_reasoning_defaults(payload: dict, model: Optional[str]) -> dict:
    if is_gpt5_model(model):
        payload["reasoning_effort"] = "low"
    return payload


def extract_chat_message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    parts.append(text_value.strip())
        return "\n".join(parts).strip()
    return ""


def extract_register_customer_arguments(message: dict) -> Optional[dict]:
    function_call = message.get("function_call")
    if function_call:
        try:
            return json.loads(function_call.get("arguments") or "{}")
        except Exception:
            return {}
    tool_calls = message.get("tool_calls") or []
    for tool_call in tool_calls:
        function_obj = tool_call.get("function") or {}
        if function_obj.get("name") != "register_customer":
            continue
        try:
            return json.loads(function_obj.get("arguments") or "{}")
        except Exception:
            return {}
    return None


def build_lead_confirmation(language: str) -> str:
    language = (language or "").lower()
    if "ru" in language:
        return "😊 Спасибо! Мы получили ваш номер и скоро свяжемся с вами."
    if "en" in language:
        return "😊 Thank you! We've received your number. Our team will call you very soon."
    return "😊 Raqamingizni oldik! Jamoamiz tez orada siz bilan bog'lanadi."


async def get_default_sales_manager_id(session: AsyncSession) -> Optional[int]:
    normalized_role = func.lower(func.replace(func.coalesce(user.c.role_name, cast(user.c.role, String)), "_", " "))
    result = await session.execute(
        select(user.c.id)
        .where(
            normalized_role == "sales manager",
            user.c.is_active == True,
        )
        .order_by(user.c.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def send_cognilabs_lead_notification(
    config: dict,
    customer_id: int,
    *,
    full_name: str,
    phone_number: str,
    platform: str,
    username: Optional[str],
    business_field: str,
    scheduled_time: str,
    conversation_id: int,
) -> None:
    token = (config.get("cognilabs_telegram_token") or "").strip()
    channel_id = (config.get("cognilabs_channel_id") or "").strip()
    if not token or not channel_id:
        return
    lines = [
        "🆕 <b>Yangi lead keldi</b>",
        f"👤 <b>Ism:</b> {full_name}",
        f"📞 <b>Telefon:</b> {phone_number}",
        f"🌐 <b>Platforma:</b> {platform}",
    ]
    if username:
        lines.append(f"🔗 <b>Username:</b> {username}")
    if business_field:
        lines.append(f"💼 <b>Yo'nalish:</b> {business_field}")
    if scheduled_time:
        lines.append(f"🕒 <b>Qulay vaqt:</b> {scheduled_time}")
    lines.append(f"🆔 <b>Lead ID:</b> {customer_id}")
    lines.append(f"💬 <b>Conversation ID:</b> {conversation_id}")
    reply_markup = None
    chat_url = build_public_chat_url(conversation_id, config.get("frontend_base_url"))
    if chat_url:
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "Suhbatni ko'rish",
                        "url": chat_url,
                    }
                ]
            ]
        }
    bot = Bot(
        token=token,
        request=HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=60.0,
            write_timeout=60.0,
            pool_timeout=30.0,
        ),
    )
    try:
        await bot.send_message(
            chat_id=channel_id,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception as exc:
        print(f"[cognilabsai-lead-notify] customer_id={customer_id} error: {exc}", flush=True)


async def create_crm_customer_from_lead(
    session: AsyncSession,
    conversation_id: int,
    *,
    channel: str,
    client_external_id: str,
    client_username: Optional[str],
    client_full_name: Optional[str],
    full_name: str,
    phone_number: str,
    business_field: str,
    scheduled_time: str,
    language: str,
) -> int:
    conversation = await get_conversation(session, conversation_id)
    if is_lead_cooldown_active(conversation) and conversation and conversation.get("crm_customer_id"):
        return int(conversation["crm_customer_id"])
    notes_value = "\n".join(
        value for value in [
            f"Business field: {business_field}" if business_field else None,
            f"Preferred call time: {scheduled_time}" if scheduled_time else None,
            f"Source conversation: {conversation_id}",
            f"Source channel: {channel}",
            f"External client id: {client_external_id}",
        ]
        if value
    )
    display_name = full_name.strip() or client_full_name or client_username or client_external_id
    config = await get_integration_config(session)
    create_response = await create_customer_api_record(
        session,
        CustomerAPICreateRequest(
            full_name=display_name,
            platform=channel,
            username=client_username,
            phone_number=phone_number,
            assistant_name="Cognilabs AI",
            chat_url=build_crm_chat_url(conversation_id),
            notes=notes_value,
            status="need_to_call",
            conversation_language=ConversationLanguageEnum(map_conversation_language(language).lower()),
        ),
    )
    customer_id = int(create_response.id)
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(
            crm_customer_id=customer_id,
            default_follow_up_last_step=0,
            default_follow_up_due_at=None,
            default_follow_up_last_sent_at=None,
            updated_at=utcnow(),
        )
    )
    await session.commit()
    await send_cognilabs_lead_notification(
        config,
        customer_id,
        full_name=display_name,
        phone_number=phone_number,
        platform=channel,
        username=client_username,
        business_field=business_field,
        scheduled_time=scheduled_time,
        conversation_id=conversation_id,
    )
    return customer_id


async def save_lead_state(
    session: AsyncSession,
    conversation_id: int,
    *,
    full_name: str,
    phone_number: str,
    business_field: str,
    scheduled_time: str,
    language: str = "uz",
):
    conversation = await get_conversation(session, conversation_id)
    lead_created_at = utcnow()
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(
            lead_created=True,
            client_full_name=full_name,
            lead_full_name=full_name,
            lead_phone_number=phone_number,
            lead_business_field=business_field,
            lead_scheduled_time=scheduled_time,
            last_lead_created_at=lead_created_at,
            updated_at=utcnow(),
        )
    )
    await session.commit()
    if conversation:
        await create_crm_customer_from_lead(
            session,
            conversation_id,
            channel=conversation["channel"],
            client_external_id=conversation["client_external_id"],
            client_username=conversation.get("client_username"),
            client_full_name=conversation.get("client_full_name"),
            full_name=full_name,
            phone_number=phone_number,
            business_field=business_field,
            scheduled_time=scheduled_time,
            language=language,
        )


async def generate_ai_reply(session: AsyncSession, conversation_id: int) -> Optional[str]:
    try:
        conversation = await get_conversation(session, conversation_id)
        config = await get_integration_config(session)
        api_key = config.get("openai_api_key")
        if not api_key:
            return None
        base_url = (config.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        model = config.get("openai_model") or DEFAULT_OPENAI_MODEL
        prompt = config.get("system_prompt") or "You are Cognilabs company's AI sales bot"
        lead_created = bool(conversation and conversation.get("lead_created"))

        history = await get_messages(session, conversation_id, limit=30, offset=0)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": COGNILABSAI_BEHAVIOR_PROMPT},
        ]
        if is_lead_cooldown_active(conversation) or lead_created:
            messages.append({
                "role": "system",
                "content": (
                    "LEAD ALREADY CREATED for this conversation. "
                    "Do NOT call register_customer again. "
                    "Do NOT repeat the greeting or ask for name/phone/field again. "
                    "Do NOT ask 'qanday xizmatlar qiziqtiradi' or restart the script. "
                    "Simply reply as a friendly consultant — answer questions if any, or say goodbye politely."
                ),
            })
        for item in history:
            role = "assistant" if item["sender_type"] in ("ai", "operator") else "user"
            messages.append({"role": role, "content": item["text"]})

        if lead_created:
            payload = apply_reasoning_defaults({
                "model": model,
                "messages": messages,
                "max_completion_tokens": 1200,
            }, model)
        else:
            payload = apply_reasoning_defaults({
                "model": model,
                "messages": messages,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "register_customer",
                            "description": "Register new interested customer into CRM",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "language": {"type": "string"},
                                    "scheduled_time": {"type": "string"},
                                    "client's_job": {"type": "string"},
                                    "full_name": {"type": "string"},
                                    "phone_number": {"type": "string"},
                                },
                                "required": ["language", "scheduled_time", "client's_job", "full_name", "phone_number"],
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
                "max_completion_tokens": 1200,
            }, model)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
            if response.status_code >= 400:
                print(f"OpenAI error {response.status_code}: {response.text}")
                return None
            data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        print("GPT Message Structure:", json.dumps(message, indent=2, ensure_ascii=False), flush=True)

        tool_data = extract_register_customer_arguments(message)
        if tool_data is not None:
            language = (tool_data.get("language") or "uzbek").lower()
            scheduled_time = (tool_data.get("scheduled_time") or "").strip()
            business_field = (tool_data.get("client's_job") or "").strip()
            full_name = (tool_data.get("full_name") or "").strip()
            phone_number = normalize_uzbek_phone((tool_data.get("phone_number") or "").strip())

            if is_missing_required_value(full_name):
                if "uz" in language:
                    return "Ismingizni ham yozib yuboring."
                if "ru" in language:
                    return "Пожалуйста, напишите своё имя."
                return "Please also send your name."

            if is_missing_required_value(business_field):
                if "uz" in language:
                    return "Qaysi sohada faoliyat yuritasiz?"
                if "ru" in language:
                    return "В какой сфере вы работаете?"
                return "What field do you work in?"

            if is_missing_required_value(scheduled_time):
                if "uz" in language:
                    return "Qo'ng'iroq uchun qaysi vaqt qulay bo'ladi?"
                if "ru" in language:
                    return "Во сколько вам удобно позвонить?"
                return "What time is convenient for a call?"

            if is_missing_required_value(phone_number):
                if "uz" in language:
                    return "Telefon raqamingizni yuboring."
                if "ru" in language:
                    return "Пожалуйста, отправьте номер телефона."
                return "Please send your phone number."

            try:
                await save_lead_state(
                    session,
                    conversation_id,
                    full_name=full_name,
                    phone_number=phone_number,
                    business_field=business_field,
                    scheduled_time=scheduled_time,
                    language=language,
                )
            except Exception as lead_exc:
                print(f"[cognilabsai] save_lead_state error (conversation {conversation_id}): {lead_exc}", flush=True)
            # Tool call topildi — faqat confirmation qaytariladi, boshqa hech narsa emas
            return build_lead_confirmation(language)

        # Tool call yo'q — oddiy text reply
        reply_text = sanitize_ai_reply_text(extract_chat_message_text(message))
        if reply_text:
            return reply_text
        return "😓 Botdan javob olinmadi. Iltimos, operatorga yozing."
    except Exception as exc:
        import traceback
        print(f"[cognilabsai] Error in generate_ai_reply (conversation {conversation_id}): {exc}", flush=True)
        traceback.print_exc()
        return "😓 Uzr, operator hozir aloqada emas edi. Iltimos, keyinroq urinib ko'ring."


async def maybe_send_ai_reply(session: AsyncSession, conversation_id: int):
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        return None
    config = await get_integration_config(session)
    if not bool(config.get("ai_globally_enabled", True)):
        return None
    ai_enabled_since = normalize_datetime(config.get("ai_enabled_since"))
    if ai_enabled_since is not None:
        latest_client_message_at = await get_latest_client_message_created_at(session, conversation_id)
        if latest_client_message_at is None or latest_client_message_at < ai_enabled_since:
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
    if conversation["channel"] == "website_ai":
        return await create_message(
            session,
            conversation_id=conversation_id,
            channel="website_ai",
            sender_type="ai",
            text_value=reply_text,
            client_external_id=conversation["client_external_id"],
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
            try:
                await maybe_send_ai_reply(session, conversation["id"])
            except Exception as exc:
                print(f"Instagram AI reply error for conversation {conversation['id']}: {exc}")


async def process_telegram_userbot_message(
    *,
    peer_id: str,
    sender_id: Optional[str],
    text: str,
    media_type: Optional[str],
    media_url: Optional[str],
    username: Optional[str],
    full_name: Optional[str],
    avatar_url: Optional[str],
):
    if not text and not media_url:
        return
    async with async_session_maker() as session:
        await ensure_schema(session)
        conversation = await upsert_conversation(
            session,
            channel="telegram",
            client_external_id=peer_id,
            client_username=username,
            client_full_name=full_name,
            client_avatar_url=avatar_url,
        )
        await create_message(
            session,
            conversation_id=conversation["id"],
            channel="telegram",
            sender_type="client",
            text_value=text or "",
            media_type=media_type,
            media_url=media_url,
            client_external_id=sender_id or peer_id,
        )


async def start_telegram_outbound_conversation(session: AsyncSession, peer: str, text_value: str, current_user) -> dict:
    snapshot = await telegram_userbot_manager.resolve_peer_snapshot(peer)
    conversation = await upsert_conversation(
        session,
        channel="telegram",
        client_external_id=snapshot["external_id"],
        client_username=snapshot.get("username"),
        client_full_name=snapshot.get("full_name"),
        client_avatar_url=snapshot.get("avatar_url"),
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


async def search_telegram_peer(session: AsyncSession, query: str) -> dict:
    try:
        snapshot = await telegram_userbot_manager.resolve_peer_snapshot(query)
    except Exception:
        search_results = await telegram_userbot_manager.search_peers(query, limit=1)
        if not search_results:
            raise
        snapshot = search_results[0]
    existing = await telegram_userbot_manager.find_existing_conversation(snapshot["external_id"])
    if existing:
        await upsert_conversation(
            session,
            channel="telegram",
            client_external_id=snapshot["external_id"],
            client_username=snapshot.get("username"),
            client_full_name=snapshot.get("full_name"),
            client_avatar_url=snapshot.get("avatar_url"),
        )
    return {
        "peer": query,
        "external_id": snapshot["external_id"],
        "username": snapshot.get("username"),
        "full_name": snapshot.get("full_name"),
        "avatar_url": snapshot.get("avatar_url") or (existing.get("client_avatar_url") if existing else None),
        "is_online": snapshot.get("is_online"),
        "presence_status": snapshot.get("presence_status"),
        "last_seen_at": snapshot.get("last_seen_at"),
        "existing_conversation_id": existing.get("id") if existing else None,
    }


async def search_telegram_peers(session: AsyncSession, query: str, limit: int = 10) -> dict:
    matches = await telegram_userbot_manager.search_peers(query, limit=limit)
    normalized_query = normalize_telegram_search_query(query)
    items: list[dict] = []
    for item in matches:
        existing = await telegram_userbot_manager.find_existing_conversation(item["external_id"])
        items.append(
            {
                "peer": item["peer"],
                "external_id": item["external_id"],
                "username": item.get("username"),
                "full_name": item.get("full_name"),
                "avatar_url": item.get("avatar_url") or (existing.get("client_avatar_url") if existing else None),
                "is_online": item.get("is_online"),
                "presence_status": item.get("presence_status"),
                "last_seen_at": item.get("last_seen_at"),
                "existing_conversation_id": existing.get("id") if existing else None,
            }
        )
    items.sort(key=lambda item: build_telegram_search_rank(item, normalized_query))
    return {
        "query": query,
        "items": items[:limit],
    }


async def send_follow_up_message(session: AsyncSession, conversation_id: int) -> bool:
    if not FOLLOW_UPS_ENABLED:
        return False
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        return False
    config = await get_integration_config(session)
    enabled, delay_minutes, message = get_conversation_follow_up_settings(conversation, config)
    if not enabled or not delay_minutes or not message:
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == conversation_id)
            .values(follow_up_due_at=None, updated_at=utcnow())
        )
        await session.commit()
        return False
    sent_at = utcnow()
    instagram_message_id = None
    telegram_message_id = None
    if conversation["channel"] == "instagram":
        access_token = config.get("instagram_access_token")
        if not access_token:
            return False
        try:
            instagram_message_id = await send_instagram_message(access_token, conversation["client_external_id"], message)
        except InstagramSendError as exc:
            if exc.is_permanent:
                await session.execute(
                    update(cognilabsai_conversation)
                    .where(cognilabsai_conversation.c.id == conversation_id)
                    .values(
                        follow_up_enabled=False,
                        follow_up_due_at=None,
                        follow_up_sent_at=None,
                        updated_at=utcnow(),
                    )
                )
                await session.commit()
                return False
            raise
    elif conversation["channel"] == "telegram":
        telegram_message_id = await telegram_userbot_manager.send_message(conversation["client_external_id"], message)
    else:
        return False
    await create_message(
        session,
        conversation_id=conversation_id,
        channel=conversation["channel"],
        sender_type="system",
        text_value=message,
        client_external_id=conversation["client_external_id"],
        instagram_message_id=instagram_message_id,
        telegram_message_id=telegram_message_id,
        created_at=sent_at,
    )
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(
            follow_up_sent_at=sent_at,
            follow_up_due_at=None,
            updated_at=utcnow(),
        )
    )
    await session.commit()
    updated = await get_conversation(session, conversation_id)
    if updated:
        await manager.broadcast(
            {
                "type": "conversation.updated",
                "conversation": updated,
            },
            conversation_id=conversation_id,
        )
    return True


async def send_default_instagram_follow_up_message(session: AsyncSession, conversation_id: int) -> bool:
    if not FOLLOW_UPS_ENABLED:
        return False
    conversation = await get_conversation(session, conversation_id)
    if not conversation or not is_default_instagram_follow_up_eligible(conversation):
        return False
    config = await get_integration_config(session)
    if not is_default_instagram_follow_up_globally_enabled(config):
        return False
    steps = get_default_instagram_follow_up_steps(config)
    current_step = int(conversation.get("default_follow_up_last_step") or 0)
    current_index = -1
    for index, (step_number, _, _) in enumerate(steps):
        if step_number == current_step:
            current_index = index
            break
    if current_step and current_index == -1:
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == conversation_id)
            .values(
                default_follow_up_due_at=None,
                updated_at=utcnow(),
            )
        )
        await session.commit()
        return False
    next_index = current_index + 1
    if next_index >= len(steps):
        await session.execute(
            update(cognilabsai_conversation)
            .where(cognilabsai_conversation.c.id == conversation_id)
            .values(default_follow_up_due_at=None, updated_at=utcnow())
        )
        await session.commit()
        return False
    next_step_number, _, message = steps[next_index]
    access_token = config.get("instagram_access_token")
    if not access_token:
        return False
    sent_at = utcnow()
    try:
        instagram_message_id = await send_instagram_message(access_token, conversation["client_external_id"], message)
    except InstagramSendError as exc:
        if exc.is_permanent:
            await session.execute(
                update(cognilabsai_conversation)
                .where(cognilabsai_conversation.c.id == conversation_id)
                .values(
                    default_follow_up_due_at=None,
                    default_follow_up_last_sent_at=None,
                    updated_at=utcnow(),
                )
            )
            await session.commit()
            return False
        raise
    await create_message(
        session,
        conversation_id=conversation_id,
        channel="instagram",
        sender_type="system",
        text_value=message,
        client_external_id=conversation["client_external_id"],
        instagram_message_id=instagram_message_id,
        created_at=sent_at,
    )
    base_last_message_at = normalize_datetime(conversation.get("last_message_at")) or sent_at
    next_due_at = None
    if next_index + 1 < len(steps):
        _, next_delay_minutes, _ = steps[next_index + 1]
        next_due_at = base_last_message_at + timedelta(minutes=int(next_delay_minutes))
    await session.execute(
        update(cognilabsai_conversation)
        .where(cognilabsai_conversation.c.id == conversation_id)
        .values(
            default_follow_up_last_step=next_step_number,
            default_follow_up_due_at=next_due_at,
            default_follow_up_last_sent_at=sent_at,
            updated_at=utcnow(),
        )
    )
    await session.commit()
    updated = await get_conversation(session, conversation_id)
    if updated:
        await manager.broadcast(
            {
                "type": "conversation.updated",
                "conversation": updated,
            },
            conversation_id=conversation_id,
        )
    return True


async def process_pending_follow_ups():
    if not FOLLOW_UPS_ENABLED:
        return
    async with async_session_maker() as session:
        now = utcnow()
        manual_result = await session.execute(
            select(cognilabsai_conversation.c.id)
            .where(
                cognilabsai_conversation.c.follow_up_enabled == True,
                cognilabsai_conversation.c.follow_up_due_at.is_not(None),
                cognilabsai_conversation.c.follow_up_due_at <= now,
            )
            .order_by(cognilabsai_conversation.c.follow_up_due_at.asc())
        )
        manual_conversation_ids = [row[0] for row in manual_result.all()]
        default_result = await session.execute(
            select(cognilabsai_conversation.c.id)
            .where(
                cognilabsai_conversation.c.channel == "instagram",
                cognilabsai_conversation.c.crm_customer_id.is_(None),
                cognilabsai_conversation.c.follow_up_enabled == False,
                cognilabsai_conversation.c.default_follow_up_due_at.is_not(None),
                cognilabsai_conversation.c.default_follow_up_due_at <= now,
            )
            .order_by(cognilabsai_conversation.c.default_follow_up_due_at.asc())
        )
        default_conversation_ids = [row[0] for row in default_result.all()]
    for conversation_id in manual_conversation_ids:
        try:
            async with async_session_maker() as session:
                await send_follow_up_message(session, conversation_id)
        except Exception as exc:
            print(f"Follow-up send error for conversation {conversation_id}: {exc}", flush=True)
    for conversation_id in default_conversation_ids:
        try:
            async with async_session_maker() as session:
                await send_default_instagram_follow_up_message(session, conversation_id)
        except Exception as exc:
            print(f"Default follow-up send error for conversation {conversation_id}: {exc}", flush=True)


async def follow_up_scheduler_loop():
    if not FOLLOW_UPS_ENABLED:
        return
    while True:
        try:
            await process_pending_follow_ups()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Follow-up scheduler error: {exc}", flush=True)
        await asyncio.sleep(FOLLOW_UP_POLL_INTERVAL_SECONDS)


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
    sources: list[tuple[str, bytes, str]] = []
    for file_name in sorted(os.listdir(folder_path)):
        full_path = os.path.join(folder_path, file_name)
        if not os.path.isfile(full_path):
            continue
        with open(full_path, "rb") as source:
            sources.append((file_name, source.read(), full_path))
    result = await import_instagram_conversation_sources(session, sources)
    result["source_type"] = "folder"
    return result


async def import_instagram_conversation_sources(
    session: AsyncSession,
    sources: list[tuple[str, bytes, str]],
) -> dict:
    await ensure_schema(session)
    imported_files = 0
    skipped_files = 0
    created_conversations = 0
    created_messages = 0
    for file_name, file_bytes, source_label in sources:
        file_hash = hashlib.sha256(f"{file_name}:".encode() + file_bytes).hexdigest()
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
        with io.TextIOWrapper(io.BytesIO(file_bytes), encoding="utf-8") as source:
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
                source_file=source_label,
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


async def import_instagram_conversations_upload(
    session: AsyncSession,
    upload_filename: str,
    upload_bytes: bytes,
) -> dict:
    await ensure_schema(session)
    normalized_name = (upload_filename or "").strip().lower()
    sources: list[tuple[str, bytes, str]] = []
    if normalized_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(upload_bytes)) as archive:
            for member in sorted(archive.infolist(), key=lambda item: item.filename):
                if member.is_dir():
                    continue
                member_name = os.path.basename(member.filename)
                if not member_name or member_name.startswith("."):
                    continue
                if not member_name.lower().endswith(".txt"):
                    continue
                sources.append(
                    (
                        member_name,
                        archive.read(member),
                        f"{upload_filename}:{member.filename}",
                    )
                )
        result = await import_instagram_conversation_sources(session, sources)
        result["source_type"] = "zip"
        return result
    if normalized_name.endswith(".txt"):
        sources.append((os.path.basename(upload_filename), upload_bytes, upload_filename))
        result = await import_instagram_conversation_sources(session, sources)
        result["source_type"] = "txt"
        return result
    raise ValueError("Only .zip or .txt files are supported")


async def startup_cognilabsai():
    global follow_up_scheduler_task
    async with async_session_maker() as session:
        await ensure_schema(session)
        await get_integration_config(session)
        await backfill_instagram_client_names(session)
        await refresh_global_follow_up_schedules(session)
        await refresh_default_instagram_follow_up_schedules(session)
    await telegram_userbot_manager.start()
    if FOLLOW_UPS_ENABLED and (follow_up_scheduler_task is None or follow_up_scheduler_task.done()):
        follow_up_scheduler_task = asyncio.create_task(follow_up_scheduler_loop())


async def shutdown_cognilabsai():
    global follow_up_scheduler_task
    if follow_up_scheduler_task is not None:
        follow_up_scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow_up_scheduler_task
        follow_up_scheduler_task = None
    await telegram_userbot_manager.stop()
