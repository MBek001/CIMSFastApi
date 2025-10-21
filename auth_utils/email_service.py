import requests
import random
import string
from typing import Optional
from config import BREVO_API_KEY, EMAIL_FROM


class EmailService:
    def __init__(self):
        self.api_url = "https://api.brevo.com/v3/smtp/email"
        self.api_key = BREVO_API_KEY
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
        """Brevo API orqali email yuborish"""
        try:
            payload = {
                "sender": {"name": "CIMS", "email": self.email_from},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_content,
            }

            if text_content:
                payload["textContent"] = text_content

            headers = {
                "accept": "application/json",
                "api-key": self.api_key,
                "content-type": "application/json",
            }

            response = requests.post(self.api_url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                print(f"✅ Email yuborildi: {to_email}")
                return True
            else:
                print(f"❌ Yuborishda xato: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            print(f"❌ Brevo API orqali yuborishda xatolik: {e}")
            return False

    async def send_verification_email(self, to_email: str, code: str) -> bool:
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
