from telegram import Bot
from telegram.error import TelegramError
from fastapi import UploadFile, HTTPException
import os
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

bot = Bot(token=TELEGRAM_BOT_TOKEN)


async def upload_audio_to_telegram(audio_file: UploadFile) -> str:
    """
    Audio faylni Telegramga yuklash va file_id qaytarish
    """
    try:
        # Faylni o'qish
        audio_content = await audio_file.read()

        # Telegramga yuborish
        message = await bot.send_audio(
            chat_id=TELEGRAM_CHAT_ID,
            audio=audio_content,
            filename=audio_file.filename,
            title=audio_file.filename
        )

        # File ID ni qaytarish
        return message.audio.file_id

    except TelegramError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Telegram xatolik: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Audio yuklashda xatolik: {str(e)}"
        )
    finally:
        # Faylni qayta o'qish uchun reset
        await audio_file.seek(0)


async def get_audio_url_from_telegram(file_id: str) -> str:
    """
    File ID dan audio URL olish
    """
    try:
        file = await bot.get_file(file_id)
        audio_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file.file_path}"
        return audio_url
    except TelegramError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Audio topilmadi: {str(e)}"
        )