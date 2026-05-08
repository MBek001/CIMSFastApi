import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import uuid
import re

from sqlalchemy import select

from database import async_session_maker

from cognilabsai.tables import cognilabsai_conversation, cognilabsai_global_integration
from utils.file_storage import PROFILE_IMAGES_DIR


class TelegramUserbotManager:
    def __init__(self):
        self.client = None
        self._lock = asyncio.Lock()
        self._handler_registered = False

    async def start(self):
        async with self._lock:
            config = await self._load_config()
            if not config:
                if self.client is not None:
                    await self.client.disconnect()
                    self.client = None
                return False
            if self.client is not None:
                try:
                    if self.client.is_connected():
                        return True
                    await self.client.disconnect()
                except Exception:
                    pass
                self.client = None
            try:
                from telethon import TelegramClient, events
                from telethon.sessions import StringSession
            except Exception:
                return False

            api_id = int(config["telegram_api_id"])
            api_hash = config["telegram_api_hash"]
            session_string = config["telegram_session"]
            client = TelegramClient(StringSession(session_string), api_id, api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                return False

            if not self._handler_registered:
                @client.on(events.NewMessage(incoming=True))
                async def on_new_message(event):
                    from cognilabsai.service import process_telegram_userbot_message

                    await process_telegram_userbot_message(
                        peer_id=str(event.chat_id),
                        sender_id=str(event.sender_id) if event.sender_id else None,
                        text=event.raw_text or "",
                        username=getattr(event.sender, "username", None) if getattr(event, "sender", None) else None,
                        full_name=" ".join(
                            value for value in [
                                getattr(event.sender, "first_name", None) if getattr(event, "sender", None) else None,
                                getattr(event.sender, "last_name", None) if getattr(event, "sender", None) else None,
                            ]
                            if value
                        ) or None,
                    )

                self._handler_registered = True

            self.client = client
            return True

    async def stop(self):
        async with self._lock:
            if self.client is not None:
                await self.client.disconnect()
                self.client = None

    async def restart(self):
        await self.stop()
        return await self.start()

    async def _get_client(self):
        started = await self.start()
        if not started or self.client is None:
            raise RuntimeError("Telegram userbot is not configured")
        try:
            if not self.client.is_connected():
                await self.client.connect()
        except Exception:
            await self.restart()
        if self.client is None:
            raise RuntimeError("Telegram userbot is not configured")
        return self.client

    async def send_message(self, peer: str, text: str) -> str | None:
        client = await self._get_client()
        entity = await client.get_entity(self._normalize_peer(peer))
        message = await client.send_message(entity=entity, message=text)
        return str(message.id) if message else None

    def _serialize_presence(self, status) -> dict:
        if status is None:
            return {
                "is_online": None,
                "presence_status": None,
                "last_seen_at": None,
            }
        try:
            from telethon.tl.types import (
                UserStatusLastMonth,
                UserStatusLastWeek,
                UserStatusOffline,
                UserStatusOnline,
                UserStatusRecently,
            )
        except Exception:
            return {
                "is_online": None,
                "presence_status": None,
                "last_seen_at": None,
            }
        if isinstance(status, UserStatusOnline):
            return {
                "is_online": True,
                "presence_status": "online",
                "last_seen_at": None,
            }
        if isinstance(status, UserStatusOffline):
            was_online = getattr(status, "was_online", None)
            if was_online is not None and getattr(was_online, "tzinfo", None) is None:
                was_online = was_online.replace(tzinfo=timezone.utc)
            return {
                "is_online": False,
                "presence_status": "offline",
                "last_seen_at": was_online,
            }
        if isinstance(status, UserStatusRecently):
            return {
                "is_online": False,
                "presence_status": "recently",
                "last_seen_at": None,
            }
        if isinstance(status, UserStatusLastWeek):
            return {
                "is_online": False,
                "presence_status": "last_week",
                "last_seen_at": None,
            }
        if isinstance(status, UserStatusLastMonth):
            return {
                "is_online": False,
                "presence_status": "last_month",
                "last_seen_at": None,
            }
        return {
            "is_online": None,
            "presence_status": status.__class__.__name__.lower(),
            "last_seen_at": None,
        }

    async def resolve_peer_snapshot(self, peer: str) -> dict:
        client = await self._get_client()
        entity = await self._resolve_entity(peer)
        full_name = self._extract_full_name(entity)
        avatar_url = await self._download_avatar(entity)
        return {
            "external_id": str(getattr(entity, "id", peer)),
            "username": getattr(entity, "username", None),
            "full_name": full_name,
            "avatar_url": avatar_url,
            **self._serialize_presence(getattr(entity, "status", None)),
        }

    async def search_peers(self, query: str, limit: int = 10) -> list[dict]:
        client = await self._get_client()
        try:
            from telethon.tl.functions.contacts import SearchRequest
        except Exception as exc:
            raise RuntimeError("Telegram search is not available") from exc
        normalized = self._normalize_peer(query)
        results: list[dict] = []
        seen: set[str] = set()

        async def append_entity(entity):
            if entity is None:
                return
            external_id = str(getattr(entity, "id", "") or "")
            if not external_id or external_id in seen:
                return
            seen.add(external_id)
            full_name = self._extract_full_name(entity)
            try:
                avatar_url = await self._download_avatar(entity)
            except Exception:
                avatar_url = None
            username = getattr(entity, "username", None)
            peer_value = username or external_id
            results.append(
                {
                    "peer": peer_value,
                    "external_id": external_id,
                    "username": username,
                    "full_name": full_name,
                    "avatar_url": avatar_url,
                    **self._serialize_presence(getattr(entity, "status", None)),
                }
            )

        try:
            entity = await self._resolve_entity(normalized)
            await append_entity(entity)
        except Exception:
            pass

        phone_entity = await self._resolve_phone_entity(str(query).strip())
        if phone_entity is not None:
            await append_entity(phone_entity)

        try:
            search = await client(SearchRequest(q=str(query).strip(), limit=limit))
            for entity in list(getattr(search, "users", []) or []):
                await append_entity(entity)
                if len(results) >= limit:
                    break
        except Exception:
            pass
        return results[:limit]

    async def find_existing_conversation(self, external_id: str):
        async with async_session_maker() as session:
            result = await session.execute(
                select(cognilabsai_conversation).where(
                    cognilabsai_conversation.c.channel == "telegram",
                    cognilabsai_conversation.c.client_external_id == external_id,
                )
            )
            return result.mappings().first()

    async def mark_read(self, peer: str) -> None:
        client = await self._get_client()
        entity = await self._resolve_entity(peer)
        await client.send_read_acknowledge(entity)

    async def _load_config(self) -> Optional[dict]:
        async with async_session_maker() as session:
            result = await session.execute(select(cognilabsai_global_integration))
            row = result.mappings().first()
            if not row:
                return None
            if not row.get("telegram_api_id") or not row.get("telegram_api_hash") or not row.get("telegram_session"):
                return None
            return dict(row)

    def _normalize_peer(self, peer: str):
        normalized = str(peer).strip()
        if normalized.startswith("https://t.me/"):
            normalized = normalized.rsplit("/", 1)[-1]
        if normalized.startswith("http://t.me/"):
            normalized = normalized.rsplit("/", 1)[-1]
        normalized = normalized.lstrip("@")
        if normalized.lstrip("-").isdigit():
            return int(normalized)
        return normalized

    def _normalize_phone(self, value: str) -> Optional[str]:
        digits = re.sub(r"\D+", "", value or "")
        if not digits:
            return None
        if digits.startswith("998") and len(digits) == 12:
            return f"+{digits}"
        if len(digits) == 9:
            return f"+998{digits}"
        if value.strip().startswith("+") and digits:
            return f"+{digits}"
        if 10 <= len(digits) <= 15:
            return f"+{digits}"
        return None

    def _extract_full_name(self, entity) -> Optional[str]:
        full_name = " ".join(
            value for value in [getattr(entity, "first_name", None), getattr(entity, "last_name", None)] if value
        ) or None
        if full_name == "Search Temp":
            return None
        return full_name

    async def _resolve_entity(self, peer: str):
        client = await self._get_client()
        normalized = self._normalize_peer(peer)
        try:
            return await client.get_entity(normalized)
        except Exception:
            pass
        phone_entity = await self._resolve_phone_entity(str(peer).strip())
        if phone_entity is not None:
            return phone_entity
        return await client.get_entity(normalized)

    async def _resolve_phone_entity(self, value: str):
        phone = self._normalize_phone(value)
        if not phone:
            return None
        client = await self._get_client()
        try:
            from telethon.tl.functions.contacts import DeleteContactsRequest, ImportContactsRequest
            from telethon.tl.types import InputPhoneContact
        except Exception:
            return None
        imported_users = []
        try:
            result = await client(
                ImportContactsRequest(
                    contacts=[
                        InputPhoneContact(
                            client_id=uuid.uuid4().int & ((1 << 63) - 1),
                            phone=phone,
                            first_name="Search",
                            last_name="Temp",
                        )
                    ]
                )
            )
            imported_users = list(getattr(result, "users", []) or [])
            if not imported_users:
                return None
            return imported_users[0]
        finally:
            if imported_users:
                try:
                    await client(DeleteContactsRequest(id=imported_users))
                except Exception:
                    pass

    async def _download_avatar(self, entity) -> Optional[str]:
        client = self.client
        if client is None:
            return None
        PROFILE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        entity_id = getattr(entity, "id", None)
        if not entity_id:
            return None
        base_path = PROFILE_IMAGES_DIR / f"cognilabsai_telegram_{entity_id}"
        for existing in PROFILE_IMAGES_DIR.glob(f"{base_path.name}.*"):
            if existing.is_file():
                existing.unlink()
        downloaded = await client.download_profile_photo(entity, file=str(base_path))
        if not downloaded:
            return None
        downloaded_path = Path(downloaded)
        return f"/images/profil_images/{downloaded_path.name}"


telegram_userbot_manager = TelegramUserbotManager()
