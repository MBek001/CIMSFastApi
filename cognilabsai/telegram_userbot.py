import asyncio
from pathlib import Path
from typing import Optional

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
                return True
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

    async def send_message(self, peer: str, text: str) -> str | None:
        if self.client is None:
            started = await self.start()
            if not started or self.client is None:
                raise RuntimeError("Telegram userbot is not configured")
        entity = await self.client.get_entity(self._normalize_peer(peer))
        message = await self.client.send_message(entity=entity, message=text)
        return str(message.id) if message else None

    async def resolve_peer_snapshot(self, peer: str) -> dict:
        if self.client is None:
            started = await self.start()
            if not started or self.client is None:
                raise RuntimeError("Telegram userbot is not configured")
        entity = await self.client.get_entity(self._normalize_peer(peer))
        full_name = " ".join(value for value in [getattr(entity, "first_name", None), getattr(entity, "last_name", None)] if value) or None
        avatar_url = await self._download_avatar(entity)
        return {
            "external_id": str(getattr(entity, "id", peer)),
            "username": getattr(entity, "username", None),
            "full_name": full_name,
            "avatar_url": avatar_url,
        }

    async def search_peers(self, query: str, limit: int = 10) -> list[dict]:
        if self.client is None:
            started = await self.start()
            if not started or self.client is None:
                raise RuntimeError("Telegram userbot is not configured")
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
            full_name = " ".join(value for value in [getattr(entity, "first_name", None), getattr(entity, "last_name", None)] if value) or None
            avatar_url = await self._download_avatar(entity)
            username = getattr(entity, "username", None)
            peer_value = username or external_id
            results.append(
                {
                    "peer": peer_value,
                    "external_id": external_id,
                    "username": username,
                    "full_name": full_name,
                    "avatar_url": avatar_url,
                }
            )

        try:
            entity = await self.client.get_entity(normalized)
            await append_entity(entity)
        except Exception:
            pass

        search = await self.client(SearchRequest(q=str(query).strip(), limit=limit))
        for entity in list(getattr(search, "users", []) or []):
            await append_entity(entity)
            if len(results) >= limit:
                break
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

    async def _download_avatar(self, entity) -> Optional[str]:
        if self.client is None:
            return None
        PROFILE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        entity_id = getattr(entity, "id", None)
        if not entity_id:
            return None
        base_path = PROFILE_IMAGES_DIR / f"cognilabsai_telegram_{entity_id}"
        for existing in PROFILE_IMAGES_DIR.glob(f"{base_path.name}.*"):
            if existing.is_file():
                existing.unlink()
        downloaded = await self.client.download_profile_photo(entity, file=str(base_path))
        if not downloaded:
            return None
        downloaded_path = Path(downloaded)
        return f"/images/profil_images/{downloaded_path.name}"


telegram_userbot_manager = TelegramUserbotManager()
