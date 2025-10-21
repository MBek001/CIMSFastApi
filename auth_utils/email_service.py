import aiosmtplib
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from config import SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, EMAIL_FROM


class EmailService:
    def __init__(self):
        self.smtp_host = SMTP_HOST
        self.smtp_port = SMTP_PORT
        self.smtp_username = SMTP_USERNAME
        self.smtp_password = SMTP_PASSWORD
        self.email_from = EMAIL_FROM

    def generate_verification_code(self, length: int = 4) -> str:
        """4 xonali tasdiqlash kodini yaratish"""
        return ''.join(random.choices(string.digits, k=length))

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """Brevo SMTP orqali email yuborish"""
        try:
            # Xabar tayyorlash
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = self.email_from
            message["To"] = to_email

            if text_content:
                message.attach(MIMEText(text_content, "plain", "utf-8"))

            message.attach(MIMEText(html_content, "html", "utf-8"))

            # Brevo serveriga ulanib yuborish
            await aiosmtplib.send(
                message,
                hostname=self.smtp_host,
                port=self.smtp_port,
                start_tls=True,  # TLS yoqilgan
                username=self.smtp_username,
                password=self.smtp_password,
                timeout=15,  # qo'shimcha xavfsizlik
            )

            print(f"✅ Email yuborildi: {to_email}")
            return True

        except Exception as e:
            print(f"❌ Email yuborishda xatolik: {e}")
            return False

    async def send_verification_email(self, to_email: str, code: str) -> bool:
        """Email tasdiqlash kodi yuborish"""
        subject = "Email Tasdiqlash - CIMS"
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #333;">🔐 Email Tasdiqlash</h2>
            <p>Assalomu alaykum!</p>
            <p>CIMS tizimida ro'yxatdan o'tish uchun quyidagi kodni kiriting:</p>
            <div style="background: #007bff; color: white; font-size: 24px; font-weight: bold; text-align: center; padding: 15px; border-radius: 8px; margin: 20px 0;">{code}</div>
            <p><strong>Muhim:</strong> Bu kod 1 daqiqa amal qiladi.</p>
            <p>Hurmat bilan, <strong>CIMS jamoasi</strong></p>
        </div>
        """
        return await self.send_email(to_email, subject, html_content)

    async def send_password_reset_email(self, to_email: str, code: str) -> bool:
        """Parol tiklash kodi yuborish"""
        subject = "Parol Tiklash - CIMS"
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #333;">🔑 Parol Tiklash</h2>
            <p>Assalomu alaykum!</p>
            <p>Parolingizni tiklash uchun quyidagi kodni kiriting:</p>
            <div style="background: #dc3545; color: white; font-size: 24px; font-weight: bold; text-align: center; padding: 15px; border-radius: 8px; margin: 20px 0;">{code}</div>
            <p><strong>Muhim:</strong> Bu kod 1 daqiqa amal qiladi.</p>
            <p>Hurmat bilan, <strong>CIMS jamoasi</strong></p>
        </div>
        """
        return await self.send_email(to_email, subject, html_content)


email_service = EmailService()
