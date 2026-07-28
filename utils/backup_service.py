import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

from config import (
    DB_NAME,
    DB_USER,
    DB_PASSWORD,
    DB_HOST,
    DB_PORT,
    CIMS_BACKUP_BOT_TOKEN,
    CIMS_BACKUP_CHAT_ID,
    CIMS_BACKUP_THREAD_ID,
    CIMS_BACKUP_MEDIA_PATHS,
)

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=5))

backup_bot_app: Optional[Application] = None
backup_lock = asyncio.Lock()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
logging.getLogger("httpx").setLevel(logging.WARNING)


def _chat_target(chat_id: str):
    return int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id


def _thread_id() -> Optional[int]:
    value = str(CIMS_BACKUP_THREAD_ID or "").strip()
    return int(value) if value.isdigit() else None


def _bot() -> Optional[Bot]:
    if not CIMS_BACKUP_BOT_TOKEN:
        return None
    return Bot(
        token=CIMS_BACKUP_BOT_TOKEN,
        request=HTTPXRequest(
            connection_pool_size=8,
            read_timeout=300.0,
            write_timeout=300.0,
            connect_timeout=30.0,
            pool_timeout=30.0,
        ),
    )


def _pg_env() -> dict:
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD or ""
    return env


def _pg_dump_command(format_value: str, output_path: Path) -> list[str]:
    return [
        "pg_dump",
        "-h", DB_HOST or "db",
        "-p", str(DB_PORT or 5432),
        "-U", DB_USER or "postgres",
        "-d", DB_NAME or "postgres",
        "-F", format_value,
        "-f", str(output_path),
    ]


def _run_command(command: list[str]) -> None:
    result = subprocess.run(command, env=_pg_env(), capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="ignore") or "pg_dump xato")


def _has_files(path: Path) -> bool:
    return path.exists() and any(item.is_file() for item in path.rglob("*"))


def _media_paths() -> list[Path]:
    raw_paths = CIMS_BACKUP_MEDIA_PATHS or "images,files"
    paths = []
    for raw_path in raw_paths.split(","):
        raw_path = raw_path.strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if _has_files(path):
            paths.append(path.resolve())
    return paths


def _create_media_zip(output_path: Path) -> Optional[Path]:
    paths = _media_paths()
    if not paths:
        return None
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root_path in paths:
            base_name = root_path.name
            for file_path in root_path.rglob("*"):
                if not file_path.is_file() or file_path.name.startswith("."):
                    continue
                archive.write(file_path, Path(base_name) / file_path.relative_to(root_path))
    if output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        return None
    return output_path


def _create_backup_files(tmp_dir: Path, now: datetime) -> list[Path]:
    stamp = now.strftime("%Y-%m-%d_%H-%M")
    dump_path = tmp_dir / f"cims_backup_{stamp}.dump"
    sql_path = tmp_dir / f"cims_backup_{stamp}.sql"
    media_zip_path = tmp_dir / f"cims_media_{stamp}.zip"

    _run_command(_pg_dump_command("c", dump_path))
    _run_command(_pg_dump_command("p", sql_path))

    files = [dump_path, sql_path]
    media_zip = _create_media_zip(media_zip_path)
    if media_zip:
        files.append(media_zip)

    for path in files:
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"Backup fayli bo'sh: {path.name}")
    return files


async def _send_backup_error(text: str) -> None:
    bot = _bot()
    if not bot or not CIMS_BACKUP_CHAT_ID:
        return
    try:
        await bot.send_message(
            chat_id=_chat_target(CIMS_BACKUP_CHAT_ID),
            message_thread_id=_thread_id(),
            text=text,
        )
    except Exception:
        pass


async def send_cims_backup(source: str = "auto") -> None:
    if not CIMS_BACKUP_BOT_TOKEN or not CIMS_BACKUP_CHAT_ID:
        print("[backup] CIMS_BACKUP_BOT_TOKEN yoki CIMS_BACKUP_CHAT_ID yo'q")
        return
    if backup_lock.locked():
        await _send_backup_error("⚠️ CIMS backup hozir ishlayapti, parallel ishga tushmadi")
        return

    async with backup_lock:
        now = datetime.now(tz=TZ)
        tmp_dir = Path(tempfile.mkdtemp(prefix="cims_backup_"))
        bot = _bot()
        try:
            files = await asyncio.get_running_loop().run_in_executor(None, _create_backup_files, tmp_dir, now)
            caption_base = (
                f"✅ CIMS backup\n"
                f"📅 {now.strftime('%Y-%m-%d %H:%M')} (Toshkent)\n"
                f"🔁 Manba: {source}"
            )
            for path in files:
                size_mb = path.stat().st_size / (1024 * 1024)
                with path.open("rb") as document:
                    await bot.send_document(
                        chat_id=_chat_target(CIMS_BACKUP_CHAT_ID),
                        message_thread_id=_thread_id(),
                        document=document,
                        filename=path.name,
                        caption=f"{caption_base}\n📦 {path.name}: {size_mb:.2f} MB",
                    )
        except Exception as exc:
            error_msg = f"❌ CIMS backup xato:\n{exc}"
            print(f"[backup] {error_msg}")
            await _send_backup_error(error_msg)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def send_daily_backup() -> None:
    await send_cims_backup("auto 03:00")


async def _manual_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    expected_chat_id = str(CIMS_BACKUP_CHAT_ID or "").strip()
    current_chat_id = str(chat.id)
    expected_thread_id = _thread_id()
    current_thread_id = message.message_thread_id
    if expected_chat_id and current_chat_id != expected_chat_id:
        return
    if expected_thread_id and current_thread_id != expected_thread_id:
        return
    await message.reply_text("CIMS backup boshlandi")
    await send_cims_backup("manual /cims_backup_bervor")


async def start_backup_bot() -> None:
    global backup_bot_app
    if backup_bot_app or not CIMS_BACKUP_BOT_TOKEN:
        return
    backup_bot_app = Application.builder().token(CIMS_BACKUP_BOT_TOKEN).build()
    backup_bot_app.add_handler(CommandHandler("cims_backup_bervor", _manual_backup_command))
    await backup_bot_app.initialize()
    await backup_bot_app.start()
    await backup_bot_app.updater.start_polling(drop_pending_updates=True)
    print("[backup] Manual backup bot ishga tushdi")


async def shutdown_backup_bot() -> None:
    global backup_bot_app
    if not backup_bot_app:
        return
    await backup_bot_app.updater.stop()
    await backup_bot_app.stop()
    await backup_bot_app.shutdown()
    backup_bot_app = None
