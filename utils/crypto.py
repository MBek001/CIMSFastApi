# utils/crypto.py
import base64
import os
from cryptography.fernet import Fernet
from hashlib import pbkdf2_hmac

def derive_fernet_key_from_password(password: str, salt: bytes = b"cims_customer_salt") -> bytes:
    key = pbkdf2_hmac('sha256', password.encode(), salt, 390000, dklen=32)
    return base64.urlsafe_b64encode(key)

def get_fernet() -> Fernet:
    password = os.getenv("FERNET_PASSWORD")
    if not password:
        raise RuntimeError("❌ FERNET_PASSWORD .env faylda topilmadi!")
    key = derive_fernet_key_from_password(password)
    return Fernet(key)

fernet = get_fernet()

def encrypt_text(text: str) -> str:
    if text is None:
        return None
    return fernet.encrypt(text.encode()).decode()

def decrypt_text(token: str) -> str:
    if token is None:
        return None
    try:
        return fernet.decrypt(token.encode()).decode()
    except Exception:
        return "[DECRYPT ERROR]"
