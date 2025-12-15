from datetime import datetime, timedelta
from typing import Optional, Union
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete
from models.user_models import user, user_page_permission, UserRole, PageName, refresh_token
from database import get_async_session
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS
import secrets

# Ikki xil authentication usuli
security = HTTPBearer(auto_error=False)  # Token kiritish uchun
# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)  # Login form uchun

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Parolni tekshirish"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    # 72 baytdan uzun bo‘lsa, kesamiz
    if len(password.encode('utf-8')) > 72:
        password = password[:72]
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
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_async_session)
):
    """Foydalanuvchini token orqali aniqlash"""
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token noto'g'ri yoki mavjud emas",
        headers={"WWW-Authenticate": "Bearer"},
    )

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
    """Faol foydalanuvchini tekshirish"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Foydalanuvchi faol emas")
    return current_user


# ========================================
# REFRESH TOKEN FUNCTIONS (NEW)
# ========================================

def create_refresh_token(user_id: int, device_info: Optional[str] = None) -> tuple[str, datetime]:
    """
    Create a new refresh token
    Returns: (token_string, expires_at_datetime)
    """
    # Generate secure random token
    token_string = secrets.token_urlsafe(64)

    # Calculate expiry (15 days)
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    return token_string, expires_at


async def store_refresh_token(
    session: AsyncSession,
    user_id: int,
    token_string: str,
    expires_at: datetime,
    device_info: Optional[str] = None
) -> int:
    """
    Store refresh token in database
    Returns: token_id
    """
    result = await session.execute(
        insert(refresh_token).values(
            user_id=user_id,
            token=token_string,
            expires_at=expires_at,
            created_at=datetime.utcnow(),
            is_active=True,
            device_info=device_info
        ).returning(refresh_token.c.id)
    )
    await session.commit()
    return result.scalar()


async def verify_refresh_token(session: AsyncSession, token_string: str) -> Optional[int]:
    """
    Verify refresh token and return user_id
    Returns: user_id if valid, None if invalid
    """
    result = await session.execute(
        select(refresh_token)
        .where(
            (refresh_token.c.token == token_string) &
            (refresh_token.c.is_active == True) &
            (refresh_token.c.expires_at > datetime.utcnow())
        )
    )
    token_row = result.fetchone()

    if not token_row:
        return None

    return token_row.user_id


async def revoke_refresh_token(session: AsyncSession, token_string: str):
    """
    Revoke/invalidate a refresh token
    """
    await session.execute(
        update(refresh_token)
        .where(refresh_token.c.token == token_string)
        .values(is_active=False)
    )
    await session.commit()


async def revoke_all_user_tokens(session: AsyncSession, user_id: int):
    """
    Revoke all refresh tokens for a user (logout from all devices)
    """
    await session.execute(
        update(refresh_token)
        .where(refresh_token.c.user_id == user_id)
        .values(is_active=False)
    )
    await session.commit()


async def cleanup_expired_tokens(session: AsyncSession):
    """
    Delete expired refresh tokens (cleanup job)
    """
    await session.execute(
        delete(refresh_token)
        .where(refresh_token.c.expires_at < datetime.utcnow())
    )
    await session.commit()
