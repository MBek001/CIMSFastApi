from telegram import Bot
from telegram.error import TelegramError
from telegram.request import HTTPXRequest
from fastapi import UploadFile, HTTPException
import io
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Timeout sozlamalarini oshirish
request = HTTPXRequest(
    connection_pool_size=8,
    connect_timeout=60.0,      # Ulanish timeout: 60 soniya
    read_timeout=180.0,         # O'qish timeout: 180 soniya (3 daqiqa)
    write_timeout=180.0,        # Yozish timeout: 180 soniya (3 daqiqa)
    pool_timeout=60.0
)

bot = Bot(token=TELEGRAM_BOT_TOKEN, request=request)


async def upload_audio_to_telegram(audio_file: UploadFile) -> str:
    """
    Audio faylni Telegramga yuklash va file_id qaytarish
    Barcha audio formatlarni qo'llab-quvvatlaydi: MP3, OGG, M4A, WAV, FLAC
    Timeout: 3 daqiqa (katta fayllar uchun)
    """
    try:
        # Faylni o'qish
        audio_content = await audio_file.read()

        # Fayl formatini aniqlash
        file_extension = ''
        if audio_file.filename:
            file_extension = audio_file.filename.split('.')[-1].lower()

        content_type = audio_file.content_type or ''

        # OGG va OPUS formatlar uchun send_voice ishlatish
        if file_extension in ['ogg', 'opus', 'oga'] or 'ogg' in content_type:
            message = await bot.send_voice(
                chat_id=TELEGRAM_CHAT_ID,
                voice=io.BytesIO(audio_content),
                filename=audio_file.filename or 'audio.ogg',
                read_timeout=180,    # 3 daqiqa
                write_timeout=180
            )
            return message.voice.file_id

        # MP3, M4A, WAV, FLAC uchun send_audio
        elif file_extension in ['mp3', 'm4a', 'wav', 'flac', 'aac', 'wma']:
            message = await bot.send_audio(
                chat_id=TELEGRAM_CHAT_ID,
                audio=io.BytesIO(audio_content),
                filename=audio_file.filename or 'audio.mp3',
                title=audio_file.filename or 'Audio File',
                read_timeout=180,    # 3 daqiqa
                write_timeout=180
            )
            return message.audio.file_id

        # Noma'lum formatlar uchun send_document
        else:
            message = await bot.send_document(
                chat_id=TELEGRAM_CHAT_ID,
                document=io.BytesIO(audio_content),
                filename=audio_file.filename or 'audio_file',
                read_timeout=180,    # 3 daqiqa
                write_timeout=180
            )
            return message.document.file_id

    except TelegramError as e:
        # Timeout xatosini aniqroq ko'rsatish
        if "timed out" in str(e).lower():
            raise HTTPException(
                status_code=504,
                detail="Telegram serveriga ulanishda timeout. Fayl juda katta yoki internet sekin. Qayta urinib ko'ring."
            )
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
    File ID dan audio URL olish (barcha formatlar uchun)
    """
    try:
        file = await bot.get_file(file_id, read_timeout=60)
        audio_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file.file_path}"
        return audio_url
    except TelegramError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Audio topilmadi: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Xatolik: {str(e)}"
        )


def validate_audio_file(audio: UploadFile) -> bool:
    """
    Audio faylni validatsiya qilish
    """
    # Content-type tekshiruvi
    allowed_types = [
        'audio/',                   # Barcha audio/* turlar
        'application/ogg',          # OGG fayllar
        'application/octet-stream'  # Ba'zi brauzerlar shunday yuboradi
    ]

    is_audio = any(
        audio.content_type.startswith(t) if audio.content_type else False
        for t in allowed_types
    )

    # Fayl kengaytmasidan tekshirish
    if audio.filename:
        file_ext = audio.filename.split('.')[-1].lower()
        audio_extensions = ['mp3', 'ogg', 'wav', 'm4a', 'flac', 'aac', 'opus', 'wma', 'oga']
        is_audio = is_audio or file_ext in audio_extensions

    return is_audio