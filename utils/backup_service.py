import asyncio
import gzip
import os
import shutil
import subprocess
import tempfile
from datetime import datetime

from sqlalchemy import select
from telegram import Bot

from config import (
    DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT,
    TELEGRAM_RECALL_BOT_TOKEN,
)
from database import async_session_maker
from models.admin_models import recall_bot_admin

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=5))


async def _get_admin_chat_ids() -> list[str]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(recall_bot_admin.c.chat_id).where(
                recall_bot_admin.c.is_active == True
            )
        )
        return [row[0] for row in result.fetchall()]


def _create_backup(path: str) -> None:
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD or ""

    dump_cmd = [
        "pg_dump",
        "-h", DB_HOST or "db",
        "-p", str(DB_PORT or 5432),
        "-U", DB_USER or "postgres",
        "-d", DB_NAME or "postgres",
        "-F", "p",
    ]

    result = subprocess.run(
        dump_cmd,
        env=env,
        capture_output=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"pg_dump xato: {result.stderr.decode()}")

    with gzip.open(path, "wb") as f:
        f.write(result.stdout)

    if os.path.getsize(path) == 0:
        raise RuntimeError("Backup fayli bo'sh yaratildi")


async def send_daily_backup() -> None:
    if not TELEGRAM_RECALL_BOT_TOKEN:
        print("[backup] TELEGRAM_RECALL_BOT_TOKEN yo'q, o'tkazib yuborildi")
        return

    bot = Bot(token=TELEGRAM_RECALL_BOT_TOKEN)
    now = datetime.now(tz=TZ)
    filename = f"cims_backup_{now.strftime('%Y-%m-%d')}.sql.gz"

    tmp_dir = tempfile.mkdtemp(prefix="cims_backup_")
    backup_path = os.path.join(tmp_dir, filename)

    chat_ids = await _get_admin_chat_ids()
    if not chat_ids:
        print("[backup] recall_bot_admin jadvalida aktiv admin yo'q")
        return

    try:
        await asyncio.get_event_loop().run_in_executor(None, _create_backup, backup_path)

        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        caption = (
            f"✅ CIMS kunlik backup\n"
            f"📅 {now.strftime('%Y-%m-%d %H:%M')} (Toshkent)\n"
            f"📦 Hajmi: {size_mb:.2f} MB"
        )

        with open(backup_path, "rb") as f:
            file_bytes = f.read()

        for chat_id in chat_ids:
            try:
                await bot.send_document(
                    chat_id=int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id,
                    document=file_bytes,
                    filename=filename,
                    caption=caption,
                )
            except Exception as e:
                print(f"[backup] {chat_id} ga yuborishda xato: {e}")

    except Exception as e:
        error_msg = f"❌ CIMS backup xato:\n{e}"
        print(f"[backup] {error_msg}")
        for chat_id in chat_ids:
            try:
                await bot.send_message(
                    chat_id=int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id,
                    text=error_msg,
                )
            except Exception:
                pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
