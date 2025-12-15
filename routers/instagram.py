"""
Instagram Statistics Router
CEO Dashboard - Instagram follower tracking
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict
from pydantic import BaseModel

from database import get_async_session
from auth_utils.auth_func import get_current_active_user
from models.user_models import user, UserRole
from models.instagram_models import instagram_account, instagram_stats
from utils.instagram_service import instagram_service


router = APIRouter(prefix="/instagram", tags=["Instagram Statistics"])


# ========================================
# PYDANTIC MODELS
# ========================================

class InstagramGrowthResponse(BaseModel):
    """Instagram growth metrics"""
    current_followers: int
    last_7_days: Dict[str, int]  # {growth, growth_percentage}
    last_30_days: Dict[str, int]  # 1 month
    last_90_days: Dict[str, int]  # 3 months
    last_180_days: Dict[str, int]  # 6 months
    last_365_days: Dict[str, int]  # 1 year


class InstagramAccountSetup(BaseModel):
    """Setup Instagram account"""
    account_username: str
    instagram_business_account_id: str
    facebook_page_id: str
    access_token: str


# ========================================
# HELPER FUNCTIONS
# ========================================

async def require_ceo_access(current_user=Depends(get_current_active_user)):
    """Only CEO can access Instagram stats"""
    if current_user.role != UserRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Faqat CEO bu ma'lumotlarga kira oladi"
        )
    return current_user


# ========================================
# ENDPOINTS
# ========================================

@router.get("/growth", response_model=InstagramGrowthResponse, summary="Instagram o'sish statistikasi")
async def get_instagram_growth(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Instagram follower o'sish statistikasini olish
    - Hozirgi followerlar soni
    - Oxirgi 7 kun, 1 oy, 3 oy, 6 oy, 1 yil davridagi o'sish
    """
    # Get active Instagram account
    result = await session.execute(
        select(instagram_account)
        .where(instagram_account.c.is_active == True)
        .limit(1)
    )
    account = result.fetchone()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instagram akkaunt topilmadi. Avval /instagram/setup endpointidan akkauntni sozlang"
        )

    account_id = account.id

    # Get latest follower count
    latest_result = await session.execute(
        select(instagram_stats.c.followers_count)
        .where(instagram_stats.c.account_id == account_id)
        .order_by(instagram_stats.c.date.desc())
        .limit(1)
    )
    latest_stat = latest_result.fetchone()

    if not latest_stat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instagram statistika topilmadi. Avval /instagram/sync endpointidan sinxronlang"
        )

    current_followers = latest_stat.followers_count

    # Calculate growth for different periods
    growth_7_days = await instagram_service.get_follower_growth(session, account_id, 7)
    growth_30_days = await instagram_service.get_follower_growth(session, account_id, 30)
    growth_90_days = await instagram_service.get_follower_growth(session, account_id, 90)
    growth_180_days = await instagram_service.get_follower_growth(session, account_id, 180)
    growth_365_days = await instagram_service.get_follower_growth(session, account_id, 365)

    return InstagramGrowthResponse(
        current_followers=current_followers,
        last_7_days={
            "growth": growth_7_days["growth"],
            "growth_percentage": growth_7_days["growth_percentage"]
        },
        last_30_days={
            "growth": growth_30_days["growth"],
            "growth_percentage": growth_30_days["growth_percentage"]
        },
        last_90_days={
            "growth": growth_90_days["growth"],
            "growth_percentage": growth_90_days["growth_percentage"]
        },
        last_180_days={
            "growth": growth_180_days["growth"],
            "growth_percentage": growth_180_days["growth_percentage"]
        },
        last_365_days={
            "growth": growth_365_days["growth"],
            "growth_percentage": growth_365_days["growth_percentage"]
        }
    )


@router.post("/sync", summary="Instagram ma'lumotlarini sinxronlash")
async def sync_instagram(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Instagram dan hozirgi follower sonini olib, bazaga saqlash
    Har kuni 1 marta ishlatilishi kerak (cron job orqali)
    """
    # Get active account
    result = await session.execute(
        select(instagram_account.c.id)
        .where(instagram_account.c.is_active == True)
        .limit(1)
    )
    account = result.fetchone()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instagram akkaunt topilmadi"
        )

    try:
        stats = await instagram_service.sync_instagram_data(session, account.id)
        return {
            "message": "Instagram ma'lumotlari muvaffaqiyatli sinxronlandi",
            "followers_count": stats["followers_count"],
            "following_count": stats["following_count"],
            "media_count": stats["media_count"]
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/setup", summary="Instagram akkauntni sozlash")
async def setup_instagram_account(
    account_data: InstagramAccountSetup,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Instagram Business akkauntni sozlash
    Kerakli ma'lumotlar:
    - account_username: Instagram username
    - instagram_business_account_id: IG Business Account ID
    - facebook_page_id: Facebook Page ID
    - access_token: Long-lived access token (60 days)
    """
    from sqlalchemy import insert, update
    from datetime import datetime, timedelta

    # Check if account already exists
    result = await session.execute(
        select(instagram_account)
        .where(instagram_account.c.account_username == account_data.account_username)
    )
    existing_account = result.fetchone()

    token_expires_at = datetime.utcnow() + timedelta(days=60)  # Long-lived token valid for 60 days

    if existing_account:
        # Update existing account
        await session.execute(
            update(instagram_account)
            .where(instagram_account.c.id == existing_account.id)
            .values(
                instagram_business_account_id=account_data.instagram_business_account_id,
                facebook_page_id=account_data.facebook_page_id,
                access_token=account_data.access_token,
                token_expires_at=token_expires_at,
                is_active=True,
                updated_at=datetime.utcnow()
            )
        )
        await session.commit()
        return {
            "message": f"Instagram akkaunt '{account_data.account_username}' muvaffaqiyatli yangilandi",
            "account_id": existing_account.id
        }
    else:
        # Create new account
        result = await session.execute(
            insert(instagram_account).values(
                account_username=account_data.account_username,
                instagram_business_account_id=account_data.instagram_business_account_id,
                facebook_page_id=account_data.facebook_page_id,
                access_token=account_data.access_token,
                token_expires_at=token_expires_at,
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            ).returning(instagram_account.c.id)
        )
        await session.commit()
        account_id = result.scalar()

        return {
            "message": f"Instagram akkaunt '{account_data.account_username}' muvaffaqiyatli sozlandi",
            "account_id": account_id
        }
