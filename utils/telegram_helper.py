import logging
from telegram import Bot
from telegram.error import TelegramError
from telegram.request import HTTPXRequest
from fastapi import UploadFile, HTTPException
import io
from config import TELEGRAM_AUDIO_BOT_TOKEN, TELEGRAM_AUDIO_CHAT_ID, TELEGRAM_UPDATE_BOT_TOKEN

# Log konfiguratsiyasi
logging.basicConfig(
    level=logging.INFO,  # Log darajasini INFO deb belgilaymiz
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Konsolga chiqarish
        logging.FileHandler('app.log', mode='a')  # Faylga yozish
    ]
)

# Timeout sozlamalarini oshirish
request = HTTPXRequest(
    connection_pool_size=8,
    connect_timeout=60.0,
    read_timeout=180.0,
    write_timeout=180.0,
    pool_timeout=60.0
)

# AUDIO BOT - audio fayllarni yuklash uchun
bot = Bot(token=TELEGRAM_AUDIO_BOT_TOKEN, request=request)


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

        # Log yozish: Faylni o'qiyotganini bildirgan log
        logging.info(f"Uploading audio file: {audio_file.filename}, type: {content_type}")

        # OGG va OPUS formatlar uchun send_voice ishlatish
        if file_extension in ['ogg', 'opus', 'oga'] or 'ogg' in content_type:
            message = await bot.send_voice(
                chat_id=TELEGRAM_AUDIO_CHAT_ID,
                voice=io.BytesIO(audio_content),
                filename=audio_file.filename or 'audio.ogg',
                read_timeout=180,  # 3 daqiqa
                write_timeout=180
            )
            logging.info(f"Audio file uploaded successfully. File ID: {message.voice.file_id}")
            return message.voice.file_id

        # MP3, M4A, WAV, FLAC uchun send_audio
        elif file_extension in ['mp3', 'm4a', 'wav', 'flac', 'aac', 'wma']:
            message = await bot.send_audio(
                chat_id=TELEGRAM_AUDIO_CHAT_ID,
                audio=io.BytesIO(audio_content),
                filename=audio_file.filename or 'audio.mp3',
                title=audio_file.filename or 'Audio File',
                read_timeout=180,  # 3 daqiqa
                write_timeout=180
            )
            logging.info(f"Audio file uploaded successfully. File ID: {message.audio.file_id}")
            return message.audio.file_id

        else:
            message = await bot.send_document(
                chat_id=TELEGRAM_AUDIO_CHAT_ID,
                document=io.BytesIO(audio_content),
                filename=audio_file.filename or 'audio_file',
                read_timeout=180,  # 3 daqiqa
                write_timeout=180
            )
            logging.info(f"Document uploaded successfully. File ID: {message.document.file_id}")
            return message.document.file_id

    except TelegramError as e:
        # Timeout xatosini aniqroq ko'rsatish
        logging.error(f"Telegram error: {str(e)}")
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
        # Umumiy xatolikni loglash
        logging.error(f"General error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Audio yuklashda xatolik: {str(e)}"
        )
    finally:
        await audio_file.seek(0)
        logging.info("Audio file processing completed.")


async def get_audio_url_from_telegram(file_id: str) -> str:
    """
    File ID dan audio URL olish (barcha formatlar uchun)
    """
    try:
        file = await bot.get_file(file_id, read_timeout=60)
        audio_url = f"https://api.telegram.org/file/bot{TELEGRAM_AUDIO_BOT_TOKEN}/{file.file_path}"
        logging.info(f"Audio URL generated successfully: {audio_url}")
        return audio_url
    except TelegramError as e:
        logging.error(f"Telegram error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Audio topilmadi: {str(e)}"
        )
    except Exception as e:
        logging.error(f"General error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Xatolik: {str(e)}"
        )


async def send_card_assignment_notification(
    chat_id: str,
    title: str,
    description: str | None,
    priority: str,
    due_date,
    assigner_name: str,
    project_name: str | None = None,
) -> None:
    if not TELEGRAM_UPDATE_BOT_TOKEN:
        return
    try:
        lines = ["📋 <b>Sizga yangi task berildi!</b>", ""]
        if project_name:
            lines.append(f"🗂 <b>Project:</b> {project_name}")
        lines.append(f"📌 <b>Task:</b> {title}")
        if description:
            lines.append(f"📝 <b>Tavsif:</b> {description}")
        lines.append(f"🎯 <b>Priority:</b> {priority.capitalize()}")
        if due_date:
            lines.append(f"📅 <b>Muddat:</b> {due_date}")
        lines.append(f"👤 <b>Kim berdi:</b> {assigner_name}")

        update_bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN, request=request)
        await update_bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
        )
    except Exception as e:
        logging.warning(f"Card assignment notification yuborishda xatolik: {e}")


def validate_audio_file(audio: UploadFile) -> bool:
    """
    Audio faylni validatsiya qilish
    """
    # Content-type tekshiruvi
    allowed_types = [
        'audio/',  # Barcha audio/* turlar
        'application/ogg',  # OGG fayllar
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

    logging.info(f"Audio file validation result: {is_audio} for file: {audio.filename}")
    return is_audio
