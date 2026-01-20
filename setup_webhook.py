#!/usr/bin/env python3
"""
Telegram Bot Webhook Setup Script
Bu skript Telegram UPDATE botga webhook o'rnatadi va sozlaydi
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

# .env faylini yuklash
load_dotenv()

# Konfiguratsiya - UPDATE BOT uchun
TELEGRAM_UPDATE_BOT_TOKEN = os.getenv('TELEGRAM_UPDATE_BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')


async def setup_webhook():
    """Telegram bot uchun webhookni o'rnatadi"""
    if not TELEGRAM_UPDATE_BOT_TOKEN:
        print("❌ XATO: TELEGRAM_UPDATE_BOT_TOKEN .env faylida topilmadi!")
        print("   @BotFather dan bot token oling va .env ga qo'shing")
        return False

    if not WEBHOOK_URL:
        print("❌ XATO: WEBHOOK_URL .env faylida topilmadi!")
        print("   Server domeningizni .env ga qo'shing (masalan: https://yourdomain.com)")
        return False

    # Webhook URLni to'g'rilash
    webhook_endpoint = f"{WEBHOOK_URL.rstrip('/')}/update-tracking/telegram-webhook"

    try:
        print(f"🤖 Bot bilan ulanish...")
        bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)

        # Bot ma'lumotlarini olish
        bot_info = await bot.get_me()
        print(f"✅ Bot topildi: @{bot_info.username} ({bot_info.first_name})")
        print(f"   Bot ID: {bot_info.id}")

        # Hozirgi webhook ma'lumotlarini tekshirish
        print(f"\n📡 Hozirgi webhook holatini tekshirish...")
        webhook_info = await bot.get_webhook_info()

        if webhook_info.url:
            print(f"   Hozirgi webhook: {webhook_info.url}")
            print(f"   Pending yangilanishlar: {webhook_info.pending_update_count}")
            if webhook_info.last_error_message:
                print(f"   ⚠️  So'nggi xato: {webhook_info.last_error_message}")
        else:
            print(f"   ℹ️  Webhook hali o'rnatilmagan")

        # Webhookni o'rnatish
        print(f"\n🔧 Webhookni o'rnatish: {webhook_endpoint}")
        success = await bot.set_webhook(
            url=webhook_endpoint,
            allowed_updates=["message", "edited_message"],
            drop_pending_updates=True,
            max_connections=40
        )

        if success:
            print(f"✅ Webhook muvaffaqiyatli o'rnatildi!")

            # Yangi webhook ma'lumotlarini tekshirish
            webhook_info = await bot.get_webhook_info()
            print(f"\n📊 Webhook ma'lumotlari:")
            print(f"   URL: {webhook_info.url}")
            print(f"   Ruxsat etilgan yangilanishlar: {webhook_info.allowed_updates}")
            print(f"   Max bog'lanishlar: {webhook_info.max_connections}")

            print(f"\n✨ Tayyor! Bot endi yangilanishlarni qabul qiladi")
            print(f"\n📝 Test qilish uchun:")
            print(f"   1. Telegram botga xabar yuboring: @{bot_info.username}")
            print(f"   2. Xabar formatı:")
            print(f"      Update for December 16")
            print(f"      #your_username")
            print(f"      - task 1")
            print(f"      - task 2")

            return True
        else:
            print(f"❌ Webhookni o'rnatishda xatolik yuz berdi")
            return False

    except TelegramError as e:
        print(f"❌ Telegram xatosi: {e}")
        return False
    except Exception as e:
        print(f"❌ Kutilmagan xato: {e}")
        return False


async def delete_webhook():
    """Webhookni o'chiradi (polling uchun)"""
    if not TELEGRAM_UPDATE_BOT_TOKEN:
        print("❌ XATO: TELEGRAM_UPDATE_BOT_TOKEN topilmadi!")
        return False

    try:
        bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)
        print(f"🗑️  Webhookni o'chirish...")
        success = await bot.delete_webhook(drop_pending_updates=True)

        if success:
            print(f"✅ Webhook muvaffaqiyatli o'chirildi!")
            return True
        else:
            print(f"❌ Webhookni o'chirishda xatolik")
            return False

    except Exception as e:
        print(f"❌ Xato: {e}")
        return False


async def check_webhook():
    """Webhook holatini tekshiradi"""
    if not TELEGRAM_UPDATE_BOT_TOKEN:
        print("❌ XATO: TELEGRAM_UPDATE_BOT_TOKEN topilmadi!")
        return False

    try:
        bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)
        bot_info = await bot.get_me()
        print(f"🤖 Bot: @{bot_info.username}")

        webhook_info = await bot.get_webhook_info()

        print(f"\n📊 Webhook ma'lumotlari:")
        url_text = webhook_info.url or "O'rnatilmagan"
        print(f"   URL: {url_text}")
        print(f"   Kutilayotgan yangilanishlar: {webhook_info.pending_update_count}")
        max_conn = webhook_info.max_connections or "N/A"
        print(f"   Max bog'lanishlar: {max_conn}")
        allowed = webhook_info.allowed_updates or "Barchasi"
        print(f"   Ruxsat etilgan: {allowed}")

        if webhook_info.last_error_date:
            from datetime import datetime
            # last_error_date timestamp (int) yoki datetime bo'lishi mumkin
            if isinstance(webhook_info.last_error_date, int):
                error_time = datetime.fromtimestamp(webhook_info.last_error_date)
            else:
                error_time = webhook_info.last_error_date
            print(f"\n⚠️  So'nggi xato ({error_time}):")
            print(f"   {webhook_info.last_error_message}")

        return True

    except Exception as e:
        print(f"❌ Xato: {e}")
        return False


async def test_bot():
    """Botning asosiy funksiyalarini test qiladi"""
    if not TELEGRAM_UPDATE_BOT_TOKEN:
        print("❌ XATO: TELEGRAM_UPDATE_BOT_TOKEN topilmadi!")
        return False

    try:
        bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)

        # Bot ma'lumotlarini olish
        bot_info = await bot.get_me()
        print(f"✅ Bot ishlayapti: @{bot_info.username}")
        print(f"   Ism: {bot_info.first_name}")
        print(f"   ID: {bot_info.id}")
        print(f"   Can join groups: {bot_info.can_join_groups}")
        print(f"   Can read messages: {bot_info.can_read_all_group_messages}")

        return True

    except Exception as e:
        print(f"❌ Bot test xatosi: {e}")
        return False


def print_usage():
    """Foydalanish yo'riqnomasini ko'rsatadi"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║         Telegram Bot Webhook Boshqaruv Vositasi              ║
╚══════════════════════════════════════════════════════════════╝

Foydalanish:
    python setup_webhook.py [komanda]

Komandalar:
    setup     - Webhookni o'rnatish (default)
    delete    - Webhookni o'chirish
    check     - Webhook holatini tekshirish
    test      - Bot ishlashini test qilish
    help      - Bu yordam xabarini ko'rsatish

Misollar:
    python setup_webhook.py              # Webhookni o'rnatish
    python setup_webhook.py check        # Holatni tekshirish
    python setup_webhook.py delete       # Webhookni o'chirish

Muhim:
    .env faylida quyidagilar bo'lishi kerak:
    - TELEGRAM_UPDATE_BOT_TOKEN=your_bot_token
    - WEBHOOK_URL=https://your-domain.com
    """)


async def main():
    """Asosiy funksiya"""
    command = sys.argv[1] if len(sys.argv) > 1 else "setup"

    if command == "help" or command == "-h" or command == "--help":
        print_usage()
        return

    print("""
╔══════════════════════════════════════════════════════════════╗
║         Telegram Bot Webhook Sozlash                         ║
╚══════════════════════════════════════════════════════════════╝
    """)

    if command == "setup":
        await setup_webhook()
    elif command == "delete":
        await delete_webhook()
    elif command == "check":
        await check_webhook()
    elif command == "test":
        await test_bot()
    else:
        print(f"❌ Noma'lum komanda: {command}")
        print(f"   'python setup_webhook.py help' ni ishga tushiring")


if __name__ == "__main__":
    asyncio.run(main())
