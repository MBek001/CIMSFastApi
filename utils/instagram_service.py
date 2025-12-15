"""
Instagram Graph API Service
Fetches follower counts and stores them in database
"""
import httpx
from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import Optional, Dict
from sqlalchemy import select, insert, update, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.instagram_models import instagram_account, instagram_stats


class InstagramService:
    """Service for Instagram Graph API integration"""

    def __init__(self):
        self.graph_api_base = "https://graph.facebook.com/v19.0"

    async def get_follower_count(
        self,
        access_token: str,
        ig_business_account_id: str
    ) -> Optional[int]:
        """
        Fetch current follower count from Instagram Graph API
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.graph_api_base}/{ig_business_account_id}",
                    params={
                        "fields": "followers_count,follows_count,media_count",
                        "access_token": access_token
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    return {
                        "followers_count": data.get("followers_count", 0),
                        "following_count": data.get("follows_count", 0),
                        "media_count": data.get("media_count", 0)
                    }
                else:
                    raise ValueError(f"Instagram API error: {response.status_code} - {response.text}")
        except Exception as e:
            raise ValueError(f"Failed to fetch Instagram data: {str(e)}")

    async def store_daily_stats(
        self,
        session: AsyncSession,
        account_id: int,
        followers_count: int,
        following_count: Optional[int] = None,
        media_count: Optional[int] = None,
        stat_date: Optional[date] = None
    ):
        """
        Store daily follower count in database
        If stat for today already exists, update it
        """
        if stat_date is None:
            stat_date = date.today()

        # Check if stat already exists for today
        result = await session.execute(
            select(instagram_stats)
            .where(
                (instagram_stats.c.account_id == account_id) &
                (instagram_stats.c.date == stat_date)
            )
        )
        existing_stat = result.fetchone()

        if existing_stat:
            # Update existing stat
            await session.execute(
                update(instagram_stats)
                .where(instagram_stats.c.id == existing_stat.id)
                .values(
                    followers_count=followers_count,
                    following_count=following_count,
                    media_count=media_count
                )
            )
        else:
            # Insert new stat
            await session.execute(
                insert(instagram_stats).values(
                    account_id=account_id,
                    date=stat_date,
                    followers_count=followers_count,
                    following_count=following_count,
                    media_count=media_count,
                    created_at=datetime.utcnow()
                )
            )

        await session.commit()

    async def get_follower_growth(
        self,
        session: AsyncSession,
        account_id: int,
        days: int
    ) -> Dict:
        """
        Calculate follower growth over specified number of days
        Returns: {start_count, end_count, growth, growth_percentage}
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # Get start stat
        start_result = await session.execute(
            select(instagram_stats.c.followers_count)
            .where(
                (instagram_stats.c.account_id == account_id) &
                (instagram_stats.c.date >= start_date)
            )
            .order_by(instagram_stats.c.date.asc())
            .limit(1)
        )
        start_stat = start_result.fetchone()

        # Get latest stat
        end_result = await session.execute(
            select(instagram_stats.c.followers_count)
            .where(instagram_stats.c.account_id == account_id)
            .order_by(desc(instagram_stats.c.date))
            .limit(1)
        )
        end_stat = end_result.fetchone()

        if not start_stat or not end_stat:
            return {
                "start_count": 0,
                "end_count": 0,
                "growth": 0,
                "growth_percentage": 0.0
            }

        start_count = start_stat.followers_count
        end_count = end_stat.followers_count
        growth = end_count - start_count
        growth_percentage = (growth / start_count * 100) if start_count > 0 else 0.0

        return {
            "start_count": start_count,
            "end_count": end_count,
            "growth": growth,
            "growth_percentage": round(growth_percentage, 2)
        }

    async def sync_instagram_data(
        self,
        session: AsyncSession,
        account_id: int
    ):
        """
        Sync Instagram data: fetch current followers and store in DB
        This should be called daily (via cron job or scheduler)
        """
        # Get account info
        result = await session.execute(
            select(instagram_account)
            .where(
                (instagram_account.c.id == account_id) &
                (instagram_account.c.is_active == True)
            )
        )
        account = result.fetchone()

        if not account:
            raise ValueError(f"Instagram account {account_id} not found or inactive")

        if not account.access_token or not account.instagram_business_account_id:
            raise ValueError(f"Instagram account {account_id} missing access token or IG Business Account ID")

        # Fetch current stats from Instagram API
        stats = await self.get_follower_count(
            access_token=account.access_token,
            ig_business_account_id=account.instagram_business_account_id
        )

        # Store in database
        await self.store_daily_stats(
            session=session,
            account_id=account_id,
            followers_count=stats["followers_count"],
            following_count=stats["following_count"],
            media_count=stats["media_count"]
        )

        # Update last synced timestamp
        await session.execute(
            update(instagram_account)
            .where(instagram_account.c.id == account_id)
            .values(last_synced_at=datetime.utcnow())
        )
        await session.commit()

        return stats


# Singleton instance
instagram_service = InstagramService()
