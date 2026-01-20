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
TELEGRAM_AUDIO_BOT_TOKEN = os.environ.get('TELEGRAM_AUDIO_BOT_TOKEN')  # Audio yuklash uchun
TELEGRAM_UPDATE_BOT_TOKEN = os.environ.get('TELEGRAM_UPDATE_BOT_TOKEN')  # Webhook/update parser uchun
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')  # Audio yuborilayotgan chat ID
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')  # Webhook URL


FREECURRENCYAPI_KEY = os.environ.get('FREECURRENCYAPI_KEY')