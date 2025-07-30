from datetime import datetime, timedelta
from typing import Optional, Union
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update
from models.user_models import user, user_page_permission, UserRole, PageName
from database import get_async_session
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

# Ikki xil authentication usuli
security = HTTPBearer(auto_error=False)  # Token kiritish uchun
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)  # Login form uchun

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Parolni tekshirish"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Parolni hashlash"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """JWT token yaratish"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(
        http_credentials: HTTPAuthorizationCredentials = Depends(security),
        oauth2_token: str = Depends(oauth2_scheme),
        session: AsyncSession = Depends(get_async_session)
):
    """Joriy foydalanuvchini olish (ikki usul orqali)"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token noto'g'ri yoki mavjud emas",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Token ni olish (HTTPBearer yoki OAuth2PasswordBearer orqali)
    token = None
    if http_credentials:
        token = http_credentials.credentials
    elif oauth2_token:
        token = oauth2_token

    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await session.execute(select(user).where(user.c.email == email))
    user_data = result.fetchone()

    if not user_data:
        raise credentials_exception

    return user_data


def get_current_active_user(current_user=Depends(get_current_user)):
    """Faol foydalanuvchini olish"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Foydalanuvchi faol emas")
    return current_user