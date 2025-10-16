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
ACCESS_TOKEN_EXPIRE_MINUTES = 3000




SMTP_HOST = os.environ.get('SMTP_HOST')
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
EMAIL_FROM = os.environ.get('EMAIL_FROM', default=SMTP_USERNAME)


VERIFICATION_CODE_EXPIRE_MINUTES = 5
PASSWORD_RESET_EXPIRE_MINUTES = 2
