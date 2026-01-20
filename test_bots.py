#!/usr/bin/env python3
"""
Telegram Botlarni Test Qilish Skripti
Bu skript ikkala botni (audio va update parser) test qiladi
"""
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from telegram import Bot
from telegram.request import HTTPXRequest

# .env faylini yuklash
load_dotenv()

# Konfiguratsiya - 2 ta alohida bot
TELEGRAM_AUDIO_BOT_TOKEN = os.getenv('TELEGRAM_AUDIO_BOT_TOKEN')
TELEGRAM_UPDATE_BOT_TOKEN = os.getenv('TELEGRAM_UPDATE_BOT_TOKEN')
TELEGRAM_AUDIO_CHAT_ID = os.getenv('TELEGRAM_AUDIO_CHAT_ID')
TELEGRAM_UPDATE_CHAT_ID = os.getenv('TELEGRAM_UPDATE_CHAT_ID')


async def test_audio_bot():
    """Audio botni test qiladi"""
    print("\n" + "="*60)
    print("🎵 AUDIO BOT TESTI")
    print("="*60)

    if not TELEGRAM_AUDIO_BOT_TOKEN:
        print("❌ XATO: TELEGRAM_AUDIO_BOT_TOKEN .env faylida topilmadi!")
        print("   @BotFather dan audio bot uchun token oling")
        return False

    if not TELEGRAM_AUDIO_CHAT_ID:
        print("❌ XATO: TELEGRAM_AUDIO_CHAT_ID .env faylida topilmadi!")
        print("   Audio yuborilayotgan guruh/kanal ID sini .env ga qo'shing")
        return False

    try:
        # Bot yaratish (telegram_helper.py kabi)
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=60.0,
            read_timeout=180.0,
            write_timeout=180.0,
            pool_timeout=60.0
        )
        bot = Bot(token=TELEGRAM_AUDIO_BOT_TOKEN, request=request)

        # Bot ma'lumotlarini olish
        bot_info = await bot.get_me()
        print(f"✅ Bot topildi: @{bot_info.username}")
        print(f"   Ism: {bot_info.first_name}")
        print(f"   ID: {bot_info.id}")

        # Test xabari yuborish
        print(f"\n📤 Test xabarini yuborish...")
        print(f"   Chat ID: {TELEGRAM_AUDIO_CHAT_ID}")

        message = await bot.send_message(
            chat_id=TELEGRAM_AUDIO_CHAT_ID,
            text="🎵 Audio Bot Test\n\nBot muvaffaqiyatli ishlamoqda!\nAudio fayllarni yuborish tayyor."
        )

        print(f"✅ Xabar yuborildi! Message ID: {message.message_id}")

        # Chat ma'lumotlarini ko'rsatish
        chat = await bot.get_chat(chat_id=TELEGRAM_AUDIO_CHAT_ID)
        print(f"\n📊 Chat ma'lumotlari:")
        print(f"   Turi: {chat.type}")
        if chat.title:
            print(f"   Nomi: {chat.title}")
        if chat.username:
            print(f"   Username: @{chat.username}")

        print(f"\n✨ Audio bot to'liq ishlayapti!")
        return True

    except Exception as e:
        print(f"❌ Audio bot xatosi: {e}")
        if "chat not found" in str(e).lower():
            print(f"\n💡 Yordam:")
            print(f"   1. Botni guruhga qo'shing")
            print(f"   2. Guruh ID sini to'g'ri kiriting")
            print(f"   3. Botga admin huquqlarini bering")
        return False


async def test_update_bot():
    """Update botni (webhook bot) test qiladi"""
    print("\n" + "="*60)
    print("📝 UPDATE BOT TESTI (Webhook)")
    print("="*60)

    if not TELEGRAM_UPDATE_BOT_TOKEN:
        print("❌ XATO: TELEGRAM_UPDATE_BOT_TOKEN .env faylida topilmadi!")
        print("   @BotFather dan update bot uchun token oling")
        return False

    try:
        # Update bot yaratish
        bot = Bot(token=TELEGRAM_UPDATE_BOT_TOKEN)

        # Bot ma'lumotlarini olish
        bot_info = await bot.get_me()
        print(f"✅ Update bot topildi: @{bot_info.username}")
        print(f"   Ism: {bot_info.first_name}")
        print(f"   ID: {bot_info.id}")

        # Webhook ma'lumotlarini olish
        webhook_info = await bot.get_webhook_info()
        print(f"\n📡 Webhook ma'lumotlari:")
        url_text = webhook_info.url or "O'rnatilmagan"
        print(f"   URL: {url_text}")
        print(f"   Pending updates: {webhook_info.pending_update_count}")

        if webhook_info.last_error_message:
            print(f"   ⚠️  So'nggi xato: {webhook_info.last_error_message}")

        print(f"\n✨ Update bot to'liq ishlayapti!")
        return True

    except Exception as e:
        print(f"❌ Update bot xatosi: {e}")
        return False


async def test_update_parser():
    """Update parser funksiyalarini test qiladi"""
    print("\n" + "="*60)
    print("🔍 UPDATE PARSER FUNKSIYALARI TESTI")
    print("="*60)

    try:
        from utils.update_parser import (
            parse_update_message,
            validate_update_content,
            extract_update_stats,
            parse_date_string
        )

        print(f"✅ Update parser modullari yuklandi")

        # Test xabari
        test_message = """Update for December 16
#testuser
- Completed project analysis
- Fixed authentication bugs
- Reviewed pull requests
- Updated documentation"""

        print(f"\n📝 Test xabarini parse qilish...")
        print(f"   Xabar:\n{test_message}\n")

        # Parse qilish
        result = parse_update_message(test_message)

        if result:
            print(f"✅ Parse muvaffaqiyatli!")
            print(f"   Username: {result['telegram_username']}")
            print(f"   Sana: {result['update_date']}")
            print(f"   Mazmun uzunligi: {len(result['update_content'])} ta belgi")

            # Validatsiya
            is_valid = validate_update_content(result['update_content'])
            print(f"\n✅ Validatsiya: {'O\'tdi' if is_valid else 'Xato'}")

            # Statistika
            stats = extract_update_stats(result['update_content'])
            print(f"\n📊 Statistika:")
            print(f"   Qatorlar: {stats['line_count']}")
            print(f"   Bullet pointlar: {stats['bullet_count']}")
            print(f"   Belgilar: {stats['character_count']}")
            print(f"   So'zlar: {stats['word_count']}")

            print(f"\n✨ Update parser bot to'liq ishlayapti!")
            return True
        else:
            print(f"❌ Parse qilishda xatolik")
            return False

    except ImportError as e:
        print(f"❌ Import xatosi: {e}")
        print(f"   utils/update_parser.py faylini tekshiring")
        return False
    except Exception as e:
        print(f"❌ Update parser xatosi: {e}")
        return False


async def test_webhook_endpoint():
    """Webhook endpoint ni test qiladi"""
    print("\n" + "="*60)
    print("🔗 WEBHOOK ENDPOINT TESTI")
    print("="*60)

    webhook_url = os.getenv('WEBHOOK_URL', 'http://localhost:8000')
    webhook_endpoint = f"{webhook_url}/update-tracking/telegram-webhook"

    print(f"📡 Webhook URL: {webhook_endpoint}")

    try:
        import httpx

        # Test payload
        test_payload = {
            "message_id": "test_123",
            "chat_id": "-1001234567890",
            "text": """Update for December 16
#testuser
- Test task 1
- Test task 2
- Test task 3""",
            "from_user": "testuser",
            "date": 1702684800
        }

        print(f"\n📤 Test so'rovini yuborish...")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                webhook_endpoint,
                json=test_payload
            )

            print(f"✅ Javob olindi: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"\n📊 Javob ma'lumotlari:")
                print(f"   Status: {data.get('status')}")
                print(f"   User ID: {data.get('user_id')}")
                print(f"   Valid: {data.get('is_valid')}")

                print(f"\n✨ Webhook endpoint ishlayapti!")
                return True
            else:
                print(f"⚠️  Server javobi: {response.status_code}")
                print(f"   {response.text}")
                return False

    except ImportError:
        print(f"⚠️  httpx kutubxonasi o'rnatilmagan")
        print(f"   O'rnatish: pip install httpx")
        return None
    except Exception as e:
        print(f"❌ Webhook test xatosi: {e}")
        if "Connection" in str(e):
            print(f"\n💡 Server ishlamayotgan bo'lishi mumkin")
            print(f"   Server ishga tushiring: python run.py")
        return False


async def check_environment():
    """Muhit sozlamalarini tekshiradi"""
    print("\n" + "="*60)
    print("⚙️  MUHIT SOZLAMALARI TEKSHIRUVI")
    print("="*60)

    env_vars = {
        'TELEGRAM_AUDIO_BOT_TOKEN': TELEGRAM_AUDIO_BOT_TOKEN,
        'TELEGRAM_UPDATE_BOT_TOKEN': TELEGRAM_UPDATE_BOT_TOKEN,
        'TELEGRAM_AUDIO_CHAT_ID': TELEGRAM_AUDIO_CHAT_ID,
        'TELEGRAM_UPDATE_CHAT_ID': TELEGRAM_UPDATE_CHAT_ID,
        'WEBHOOK_URL': os.getenv('WEBHOOK_URL'),
        'DB_NAME': os.getenv('DB_NAME'),
        'DB_HOST': os.getenv('DB_HOST'),
    }

    all_ok = True
    for key, value in env_vars.items():
        status = "✅" if value else "❌"
        display_value = value if value else "O'rnatilmagan"

        # Token va passwordlarni yashirish
        if 'TOKEN' in key or 'PASSWORD' in key:
            if value:
                display_value = value[:10] + "..." if len(value) > 10 else "***"

        print(f"{status} {key:30} = {display_value}")

        if not value and key in ['TELEGRAM_AUDIO_BOT_TOKEN', 'TELEGRAM_UPDATE_BOT_TOKEN', 'TELEGRAM_AUDIO_CHAT_ID', 'TELEGRAM_UPDATE_CHAT_ID']:
            all_ok = False

    if not all_ok:
        print(f"\n⚠️  Ba'zi muhim o'zgaruvchilar o'rnatilmagan!")
        print(f"   .env faylini to'ldiring:")
        print(f"   - TELEGRAM_AUDIO_BOT_TOKEN (audio bot tokeni)")
        print(f"   - TELEGRAM_UPDATE_BOT_TOKEN (update bot tokeni)")
        print(f"   - TELEGRAM_AUDIO_CHAT_ID (audio yuborilayotgan guruh ID)")
        print(f"   - TELEGRAM_UPDATE_CHAT_ID (yangilanishlar o'qilayotgan guruh ID)")

    return all_ok


async def run_all_tests():
    """Barcha testlarni ishga tushiradi"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║         Telegram Botlar To'liq Test Dasturi                  ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # 1. Muhitni tekshirish
    env_ok = await check_environment()
    if not env_ok:
        print(f"\n❌ Test to'xtatildi: Muhit sozlamalari to'liq emas")
        return

    # 2. Audio botni test qilish
    audio_ok = await test_audio_bot()

    # 3. Update botni test qilish
    update_ok = await test_update_bot()

    # 4. Update parser funksiyalarini test qilish
    parser_ok = await test_update_parser()

    # 5. Webhook endpointni test qilish
    webhook_ok = await test_webhook_endpoint()

    # Natijalar
    print("\n" + "="*60)
    print("📊 TEST NATIJALARI")
    print("="*60)

    results = [
        ("Muhit sozlamalari", env_ok),
        ("Audio bot", audio_ok),
        ("Update bot", update_ok),
        ("Update parser funksiyalari", parser_ok),
        ("Webhook endpoint", webhook_ok if webhook_ok is not None else "Skip"),
    ]

    for name, status in results:
        if status is True:
            print(f"✅ {name:30} - OK")
        elif status is False:
            print(f"❌ {name:30} - XATO")
        else:
            print(f"⚠️  {name:30} - O'TKAZILDI")

    # Umumiy natija
    print(f"\n{'='*60}")
    all_passed = all(r[1] is True for r in results if r[1] is not None)

    if all_passed:
        print(f"✅ Barcha testlar muvaffaqiyatli o'tdi!")
        print(f"\n📝 Keyingi qadamlar:")
        print(f"   1. Webhookni o'rnating: python setup_webhook.py setup")
        print(f"   2. Serverni ishga tushiring: python run.py")
        print(f"   3. Telegram botga test xabari yuboring")
    else:
        print(f"⚠️  Ba'zi testlar muvaffaqiyatsiz tugadi")
        print(f"\n💡 .env faylini to'ldiring va qayta urinib ko'ring")


def print_usage():
    """Foydalanish yo'riqnomasini ko'rsatadi"""
    print("""
Foydalanish:
    python test_bots.py [test_name]

Mavjud testlar:
    all       - Barcha testlarni ishga tushirish (default)
    env       - Muhit sozlamalarini tekshirish
    audio     - Audio botni test qilish
    update    - Update botni (webhook) test qilish
    parser    - Update parser funksiyalarini test qilish
    webhook   - Webhook endpointni test qilish

Misollar:
    python test_bots.py              # Barcha testlar
    python test_bots.py audio        # Faqat audio bot
    python test_bots.py update       # Faqat update bot
    python test_bots.py env          # Muhit tekshiruvi
    """)


async def main():
    """Asosiy funksiya"""
    test_name = sys.argv[1] if len(sys.argv) > 1 else "all"

    if test_name == "help" or test_name == "-h":
        print_usage()
        return

    if test_name == "all":
        await run_all_tests()
    elif test_name == "env":
        await check_environment()
    elif test_name == "audio":
        await check_environment()
        await test_audio_bot()
    elif test_name == "update":
        await check_environment()
        await test_update_bot()
    elif test_name == "parser":
        await test_update_parser()
    elif test_name == "webhook":
        await test_webhook_endpoint()
    else:
        print(f"❌ Noma'lum test: {test_name}")
        print_usage()


if __name__ == "__main__":
    asyncio.run(main())
