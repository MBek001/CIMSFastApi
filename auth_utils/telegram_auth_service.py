import logging

from telegram import Bot
from telegram.error import TelegramError

from config import PASSWORD_RESET_EXPIRE_MINUTES, TELEGRAM_UPDATE_BOT_TOKEN


def _as_telegram_chat_target(chat_id_value: str | int) -> str | int:
    if isinstance(chat_id_value, str) and chat_id_value.lstrip("-").isdigit():
        return int(chat_id_value)
    return chat_id_value


class TelegramAuthService:
    def __init__(self) -> None:
        self.bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN) if TELEGRAM_UPDATE_BOT_TOKEN else None

    async def send_password_reset_code(self, chat_id: str | int, code: str) -> bool:
        if not self.bot:
            logging.warning("Telegram update bot token topilmadi, reset kodi yuborilmadi")
            return False

        try:
            await self.bot.send_message(
                chat_id=_as_telegram_chat_target(chat_id),
                text=(
                    "Parolni tiklash kodi:\n"
                    f"{code}\n\n"
                    f"Bu kod {PASSWORD_RESET_EXPIRE_MINUTES} daqiqa amal qiladi."
                ),
            )
            return True
        except TelegramError as exc:
            logging.error("Telegram orqali reset kodi yuborilmadi: %s", exc)
            return False


telegram_auth_service = TelegramAuthService()
