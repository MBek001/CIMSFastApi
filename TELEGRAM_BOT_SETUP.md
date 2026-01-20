# 🤖 Telegram Botlar Sozlash Qo'llanmasi

Bu loyihada **2 ta ALOHIDA Telegram bot** mavjud:

1. **Audio Bot** (`TELEGRAM_AUDIO_BOT_TOKEN`) - Audio fayllarni Telegram guruhga yuboradi va saqlaydi
2. **Update Bot** (`TELEGRAM_UPDATE_BOT_TOKEN`) - Webhook orqali kunlik yangilanishlarni qabul qiladi va bazaga saqlaydi

⚠️ **MUHIM:** Ikkala bot ham TURLI botlar - har biri uchun @BotFather dan ALOHIDA token olish kerak!

## 📋 Bo'limlar

- [Tez Sozlash](#-tez-sozlash)
- [Bot Yaratish](#-telegram-bot-yaratish)
- [Sozlash](#️-sozlash)
- [Webhook O'rnatish](#-webhook-ornatish)
- [Test Qilish](#-test-qilish)
- [Muammolarni Hal Qilish](#-muammolarni-hal-qilish)

---

## 🚀 Tez Sozlash

```bash
# 1. .env faylini to'ldiring
cp .env.example .env
nano .env  # yoki istalgan muharrir

# 2. Webhook o'rnating
python setup_webhook.py setup

# 3. Test qiling
python test_bots.py all

# 4. Serverni ishga tushiring
python run.py
```

---

## 🤖 Telegram Bot Yaratish

### 1-qadam: AUDIO BOT yaratish

1. Telegramda [@BotFather](https://t.me/BotFather) ni oching
2. `/newbot` komandasi yuboring
3. Bot uchun ism kiriting (masalan: "CIMS Audio Bot")
4. Username kiriting (masalan: "cims_audio_bot")
5. **Bot token**ni saqlang - bu `TELEGRAM_AUDIO_BOT_TOKEN` bo'ladi

   Masalan: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`

### 2-qadam: UPDATE BOT yaratish

1. Yana [@BotFather](https://t.me/BotFather) ga boring
2. Yana `/newbot` komandasi yuboring
3. Boshqa ism kiriting (masalan: "CIMS Update Bot")
4. Boshqa username kiriting (masalan: "cims_update_bot")
5. **Ikkinchi bot token**ni saqlang - bu `TELEGRAM_UPDATE_BOT_TOKEN` bo'ladi

   Masalan: `0987654321:ZYXwvuTSRqponMLKjihGFEdcba`

### 3-qadam: Update botga ruxsatlar bering

BotFather da UPDATE bot uchun:

```
/mybots
[Update botingizni tanlang]
Bot Settings
Group Privacy - DISABLE (Guruh xabarlarini o'qish uchun)
```

**Eslatma:** Audio bot uchun bu sozlama shart emas (u faqat xabar yuboradi)

### 3-qadam: Chat ID ni oling

**Opsiya A: Guruh uchun**

1. Botni guruhingizga qo'shing
2. Guruhda biror xabar yozing
3. Brauzerda oching:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   ```
4. `"chat":{"id":-1001234567890` ni toping
5. Bu Chat ID (manfiy raqam bilan)

**Opsiya B: Kanal uchun**

1. Botni kanalga admin qiling
2. Kanalda xabar yuboring
3. Yuqoridagi getUpdates metodini ishlating
4. Chat ID ni oling

**Opsiya C: Shaxsiy chat uchun**

1. [@userinfobot](https://t.me/userinfobot) ga `/start` yuboring
2. Sizning Chat ID ni ko'rsatadi

---

## ⚙️ Sozlash

### .env faylini to'ldirish

`.env` faylini oching va quyidagi qismlarni to'ldiring:

```bash
# Telegram Bot Sozlamalari - 2 TA ALOHIDA BOT!

# 1. AUDIO BOT - Audio fayllarni yuklash uchun
TELEGRAM_AUDIO_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=-1001234567890  # Audio yuborilayotgan guruh/kanal ID

# 2. UPDATE BOT - Webhook va yangilanishlarni qabul qilish uchun
TELEGRAM_UPDATE_BOT_TOKEN=0987654321:ZYXwvuTSRqponMLKjihGFEdcba
WEBHOOK_URL=https://your-domain.com  # Server domeningiz (HTTPS!)
```

### Muhim eslatmalar:

- `TELEGRAM_AUDIO_BOT_TOKEN` - Audio bot uchun token (1-bot)
- `TELEGRAM_UPDATE_BOT_TOKEN` - Update bot uchun token (2-bot)
- `TELEGRAM_CHAT_ID` - Audio yuborilayotgan guruh/kanal ID (manfiy raqam)
- `WEBHOOK_URL` - Server domeni (HTTPS bo'lishi shart!)

---

## 🔗 Webhook O'rnatish

⚠️ **Muhim:** Webhook faqat UPDATE bot uchun o'rnatiladi (audio bot webhook ishlatmaydi)

Telegram UPDATE botga webhook o'rnatish uchun:

```bash
# Webhook o'rnatish
python setup_webhook.py setup

# Webhook holatini tekshirish
python setup_webhook.py check

# Webhookni o'chirish (kerak bo'lsa)
python setup_webhook.py delete

# Bot ishlashini test qilish
python setup_webhook.py test

# Yordam
python setup_webhook.py help
```

### Webhook manzil

Webhook avtomatik ravishda quyidagi manzilga o'rnatiladi:
```
https://your-domain.com/update-tracking/telegram-webhook
```

### Webhook talablari

- ✅ HTTPS bo'lishi SHART (HTTP qabul qilinmaydi)
- ✅ SSL sertifikati to'g'ri bo'lishi kerak
- ✅ Server 443 portda ishlashi kerak (yoki reverse proxy orqali)
- ✅ Tezkor javob berishi kerak (< 60 soniya)

---

## 🧪 Test Qilish

### Barcha testlarni ishga tushirish

```bash
python test_bots.py all
```

Bu quyidagilarni tekshiradi:
- ✅ Muhit sozlamalari (.env)
- ✅ Audio bot
- ✅ Update parser bot
- ✅ Webhook endpoint

### Alohida testlar

```bash
# Muhit sozlamalarini tekshirish
python test_bots.py env

# Audio botni test qilish
python test_bots.py audio

# Update parser botni test qilish
python test_bots.py parser

# Webhook endpointni test qilish
python test_bots.py webhook
```

---

## 📱 Botlardan Foydalanish

### 1. Audio Bot (`TELEGRAM_AUDIO_BOT_TOKEN`)

**Maqsad:** Audio fayllarni Telegram ga yuklaydi va file_id qaytaradi

**Fayl:** `utils/telegram_helper.py`

**Kod misoli:**
```python
from utils.telegram_helper import upload_audio_to_telegram, get_audio_url_from_telegram

# Audio yuklash
file_id = await upload_audio_to_telegram(audio_file_bytes, "recording.mp3")

# Audio URLni olish
audio_url = await get_audio_url_from_telegram(file_id)
```

**Qo'llab-quvvatlanadigan formatlar:**
- MP3, OGG, M4A, WAV, FLAC, AAC, OPUS, WMA

**Xususiyatlar:**
- ⏱️ Timeout: 180 soniya (katta fayllar uchun)
- 📦 Connection pool: 8 ta parallel ulanish
- 🔄 Avtomatik format aniqlash

### 2. Update Bot (`TELEGRAM_UPDATE_BOT_TOKEN`)

**Maqsad:** Webhook orqali Telegram guruhdan kunlik yangilanishlarni qabul qiladi

**Fayllar:**
- `routers/update_tracking.py` - Webhook endpoint
- `utils/update_parser.py` - Xabarlarni parse qilish

**Xabar formati:**
```
Update for December 16
#username
- Completed project analysis
- Fixed authentication bugs
- Reviewed pull requests
- Updated documentation
```

**Webhook avtomatik:**
- ✅ Xabarni parse qiladi
- ✅ Foydalanuvchini topadi
- ✅ Validatsiya qiladi (min 20 ta belgi)
- ✅ Dublikatlarni tekshiradi
- ✅ Bazaga saqlaydi

**API endpoint:**
```
POST /update-tracking/telegram-webhook
```

**Javob:**
```json
{
  "status": "success",
  "user_id": 123,
  "update_date": "2024-12-16",
  "is_valid": true
}
```

---

## 🔧 Muammolarni Hal Qilish

### ❌ "TELEGRAM_AUDIO_BOT_TOKEN topilmadi" yoki "TELEGRAM_UPDATE_BOT_TOKEN topilmadi"

**Sabab:** .env faylida tokenlar yo'q yoki noto'g'ri nomlanган

**Yechim:**
1. `.env` faylini oching
2. Ikkala tokenni ham qo'shing:
   ```bash
   TELEGRAM_AUDIO_BOT_TOKEN=your_audio_bot_token
   TELEGRAM_UPDATE_BOT_TOKEN=your_update_bot_token
   ```
3. Saqlang va qayta ishga tushiring

**Eslatma:** Eski `TELEGRAM_BOT_TOKEN` endi ishlamaydi - 2 ta alohida token kerak!

### ❌ "Chat not found"

**Sabab:** Chat ID noto'g'ri yoki bot guruhda emas

**Yechim:**
1. Botni guruhga qo'shing
2. Chat ID ni to'g'ri kiriting (manfiy raqam bo'lishi kerak)
3. Botga admin ruxsati bering

### ❌ "Webhook failed"

**Sabab:** HTTPS yo'q yoki SSL muammosi

**Yechim:**
1. `WEBHOOK_URL` HTTPS bo'lishi kerak
2. SSL sertifikat to'g'ri o'rnatilganini tekshiring
3. Server 443 portda ishlayotganini tekshiring
4. Firewall/reverse proxy sozlamalarini tekshiring

### ❌ "Unauthorized"

**Sabab:** Bot token noto'g'ri

**Yechim:**
1. BotFather dan tokenni qayta oling
2. `.env` faylida to'g'ri ekanlini tekshiring
3. Probel yoki qo'shimcha belgilar yo'qligini tekshiring

### ❌ "Connection timeout"

**Sabab:** Internet muammosi yoki Telegram bloklangan

**Yechim:**
1. Internet aloqani tekshiring
2. VPN ishlatib ko'ring
3. Proxy sozlang (kerak bo'lsa)

### ⚠️ "Webhook pending updates"

**Sabab:** Server to'xtaganda kutayotgan xabarlar to'plangan

**Yechim:**
```bash
# Kutayotgan yangilanishlarni tozalash
python setup_webhook.py setup
```

---

## 📊 Foydali Komandalar

```bash
# Server ishga tushirish
python run.py

# Webhook holatini ko'rish
python setup_webhook.py check

# Botni test qilish
python test_bots.py all

# Loglarni ko'rish (Docker)
docker-compose logs -f app

# Database migratsiya
alembic upgrade head

# .env faylini tekshirish
cat .env | grep TELEGRAM
```

---

## 📚 Qo'shimcha Resurslar

### Telegram Bot API Hujjatlar
- [Bot API](https://core.telegram.org/bots/api)
- [Webhook Guide](https://core.telegram.org/bots/webhooks)
- [python-telegram-bot](https://docs.python-telegram-bot.org/)

### Proyekt Fayllari
- `utils/telegram_helper.py` - Audio bot
- `utils/update_parser.py` - Update parser
- `routers/update_tracking.py` - Webhook endpoint
- `setup_webhook.py` - Webhook sozlash
- `test_bots.py` - Bot testlari

### Muhim Endpointlar

| Endpoint | Metod | Tavsif |
|----------|-------|--------|
| `/update-tracking/telegram-webhook` | POST | Telegram webhook |
| `/update-tracking/stats/user/{id}` | GET | Foydalanuvchi statistikasi |
| `/update-tracking/recent` | GET | So'nggi yangilanishlar |
| `/update-tracking/missing` | GET | Qoldirilgan yangilanishlar |

---

## 🆘 Yordam Kerakmi?

Muammo yechilmasa:

1. Loglarni tekshiring: `docker-compose logs -f app`
2. Test qiling: `python test_bots.py all`
3. Webhook holatini ko'ring: `python setup_webhook.py check`
4. [GitHub Issues](https://github.com/yourusername/yourrepo/issues) ga murojaat qiling

---

**Muvaffaqiyat!** 🎉

Botlaringiz endi ishlashga tayyor. Telegram guruhingizda test xabar yuboring!
