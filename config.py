import os
from dotenv import load_dotenv

load_dotenv()

DB_NAME = os.environ.get('DB_NAME')
DB_TYPE = os.environ.get('DB_TYPE')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_HOST = os.environ.get('DB_HOST')
DB_PORT = os.environ.get('DB_PORT')
SECRET = os.environ.get('SECRET')


SECRET_KEY = os.environ.get('SECRET_KEY', default='uzbekiston-juda-xavfsiz-kalit-1234567890')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 3000  # Access token - 50 hours (for backward compatibility)
REFRESH_TOKEN_EXPIRE_DAYS = 15  # Refresh token - 15 days

# SMTP_HOST = os.environ.get('SMTP_HOST')
# SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
# SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
# SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
# EMAIL_FROM = os.environ.get('EMAIL_FROM', default=SMTP_USERNAME)
#

# -----------------------------
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
SMTP_HOST = os.environ.get('SMTP_HOST')
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')



VERIFICATION_CODE_EXPIRE_MINUTES = 30
PASSWORD_RESET_EXPIRE_MINUTES = 30



# Telegram Bot Tokens - 2 ta alohida bot
TELEGRAM_AUDIO_BOT_TOKEN = os.environ.get('TELEGRAM_AUDIO_BOT_TOKEN')  # Audio bot tokeni
TELEGRAM_UPDATE_BOT_TOKEN = os.environ.get('TELEGRAM_UPDATE_BOT_TOKEN')  # Update bot tokeni
TELEGRAM_RECALL_BOT_TOKEN = os.environ.get('TELEGRAM_RECALL_BOT_TOKEN')  # Recall reminder bot tokeni

# Telegram Chat IDs - har bir bot uchun alohida guruh
TELEGRAM_AUDIO_CHAT_ID = os.environ.get('TELEGRAM_AUDIO_CHAT_ID')  # Audio yuborilayotgan guruh ID
TELEGRAM_UPDATE_CHAT_ID = os.environ.get('TELEGRAM_UPDATE_CHAT_ID')  # Yangilanishlar o'qilayotgan guruh ID

# Backward compatibility uchun (eski kod ishlab turishi uchun)
TELEGRAM_CHAT_ID = TELEGRAM_AUDIO_CHAT_ID  # Default: audio chat ID

WEBHOOK_URL = os.environ.get('WEBHOOK_URL')  # Webhook URL

# Update Bot Admin - Statistika uchun parol
UPDATE_ADMIN_PASSWORD = os.environ.get('UPDATE_ADMIN_PASSWORD', 'admin123')  # Default parol
RECALL_BOT_ADMIN_PASSWORD = os.environ.get('RECALL_BOT_ADMIN_PASSWORD', 'recall123')  # Recall bot admin paroli
RECALL_DAILY_STATS_HOUR = int(os.environ.get('RECALL_DAILY_STATS_HOUR', 10))  # Daily CRM stats send hour (UZ)
RECALL_DAILY_STATS_WINDOW_MINUTES = int(
    os.environ.get('RECALL_DAILY_STATS_WINDOW_MINUTES', 5)
)  # 10:00-10:04 oralig'ida yuborish oynasi
RECALL_DAILY_STATS_INTERVAL_DAYS = int(
    os.environ.get('RECALL_DAILY_STATS_INTERVAL_DAYS', 3)
)  # CRM digest yuborish oralig'i (kunlarda)


FREECURRENCYAPI_KEY = os.environ.get('FREECURRENCYAPI_KEY')

# AI Summary (optional)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
ATTENDANCE_API_KEY = os.environ.get("ATTENDANCE_API_KEY")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Google Calendar sync (optional)
GOOGLE_CALENDAR_SYNC_ENABLED = _as_bool(os.environ.get("GOOGLE_CALENDAR_SYNC_ENABLED"), False)
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")
GOOGLE_CALENDAR_TIMEZONE = os.environ.get("GOOGLE_CALENDAR_TIMEZONE", "Asia/Tashkent")
GOOGLE_CALENDAR_EVENT_DURATION_MINUTES = int(
    os.environ.get("GOOGLE_CALENDAR_EVENT_DURATION_MINUTES", 20)
)
GOOGLE_CALENDAR_EVENT_COLOR_ID = os.environ.get("GOOGLE_CALENDAR_EVENT_COLOR_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SERVICE_ACCOUNT_SUBJECT = os.environ.get("GOOGLE_SERVICE_ACCOUNT_SUBJECT")
