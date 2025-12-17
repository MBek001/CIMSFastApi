"""
Sales Statistics Router
Lead tracking with date filters and customer type filtering
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, cast, Date
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List
from pydantic import BaseModel

from database import get_async_session
from auth_utils.auth_func import get_current_active_user
from models.admin_models import customer, CustomerType
from models.user_models import user_page_permission, PageName


router = APIRouter(prefix="/sales", tags=["Sales Statistics"])


# ========================================
# PYDANTIC MODELS
# ========================================

class SalesStatsResponse(BaseModel):
    """Sales statistics response"""
    today: int
    yesterday: int
    this_week: int
    last_week: int
    customer_type: Optional[str] = None  # Filter applied


class DailySalesResponse(BaseModel):
    """Daily sales breakdown"""
    date: str
    count: int


class DetailedSalesResponse(BaseModel):
    """Detailed sales with daily breakdown"""
    summary: SalesStatsResponse
    daily_breakdown: List[DailySalesResponse]
    date_range: str


# ========================================
# HELPER FUNCTIONS
# ========================================

async def require_sales_access(
    current_user=Depends(get_current_active_user),
    session: AsyncSession = Depends(get_async_session)
):
    """Check if user has CRM access"""
    result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            (user_page_permission.c.user_id == current_user.id) &
            (user_page_permission.c.page_name == PageName.crm)
        )
    )
    permissions = result.fetchall()

    if not permissions and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=403,
            detail="Sales statistikasini ko'rish huquqingiz yo'q"
        )

    return current_user


def get_date_ranges():
    """Calculate date ranges for statistics"""
    today = date.today()
    yesterday = today - timedelta(days=1)

    # This week (Monday to Sunday)
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)  # Sunday

    # Last week
    last_week_start = week_start - timedelta(days=7)
    last_week_end = last_week_start + timedelta(days=6)

    return {
        "today": today,
        "yesterday": yesterday,
        "this_week_start": week_start,
        "this_week_end": week_end,
        "last_week_start": last_week_start,
        "last_week_end": last_week_end
    }


# ========================================
# ENDPOINTS
# ========================================

@router.get("/stats", response_model=SalesStatsResponse, summary="Sales statistika")
async def get_sales_stats(
    customer_type: Optional[str] = Query(None, description="Customer type filter: 'international' or 'local' or null for all"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_sales_access)
):
    """
    Leadlar statistikasi:
    - Bugun nechta lead keldi
    - Kecha nechta
    - Shu hafta (Monday-Sunday)
    - O'tgan hafta

    customer_type parameter:
    - null/None: Barcha leadlar
    - "international": Faqat international leadlar
    - "local": Faqat local leadlar (null ham shu yerga kiradi)
    """
    dates = get_date_ranges()

    # Build type filter
    type_filter = None
    if customer_type == "international":
        type_filter = customer.c.type == CustomerType.international
    elif customer_type == "local":
        type_filter = or_(
            customer.c.type == CustomerType.local,
            customer.c.type == None  # null values treated as local
        )

    # Count today's leads
    today_query = select(func.count()).select_from(customer).where(
        cast(customer.c.created_at, Date) == dates["today"]
    )
    if type_filter is not None:
        today_query = today_query.where(type_filter)
    today_result = await session.execute(today_query)
    today_count = today_result.scalar() or 0

    # Count yesterday's leads
    yesterday_query = select(func.count()).select_from(customer).where(
        cast(customer.c.created_at, Date) == dates["yesterday"]
    )
    if type_filter is not None:
        yesterday_query = yesterday_query.where(type_filter)
    yesterday_result = await session.execute(yesterday_query)
    yesterday_count = yesterday_result.scalar() or 0

    # Count this week's leads
    this_week_query = select(func.count()).select_from(customer).where(
        and_(
            cast(customer.c.created_at, Date) >= dates["this_week_start"],
            cast(customer.c.created_at, Date) <= dates["this_week_end"]
        )
    )
    if type_filter is not None:
        this_week_query = this_week_query.where(type_filter)
    this_week_result = await session.execute(this_week_query)
    this_week_count = this_week_result.scalar() or 0

    # Count last week's leads
    last_week_query = select(func.count()).select_from(customer).where(
        and_(
            cast(customer.c.created_at, Date) >= dates["last_week_start"],
            cast(customer.c.created_at, Date) <= dates["last_week_end"]
        )
    )
    if type_filter is not None:
        last_week_query = last_week_query.where(type_filter)
    last_week_result = await session.execute(last_week_query)
    last_week_count = last_week_result.scalar() or 0

    return SalesStatsResponse(
        today=today_count,
        yesterday=yesterday_count,
        this_week=this_week_count,
        last_week=last_week_count,
        customer_type=customer_type
    )


@router.get("/detailed", response_model=DetailedSalesResponse, summary="Batafsil sales statistika")
async def get_detailed_sales_stats(
    days: int = Query(30, ge=1, le=365, description="Necha kunlik ma'lumot ko'rsatish (1-365)"),
    customer_type: Optional[str] = Query(None, description="Customer type filter: 'international' or 'local' or null for all"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_sales_access)
):
    """
    Har kunlik lead statistikasi
    - Oxirgi N kun davomida har kuni nechta lead kelgan
    - customer_type bo'yicha filter

    Example: days=30 - oxirgi 30 kunlik har kunlik statistika
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    # Build type filter
    type_filter = None
    if customer_type == "international":
        type_filter = customer.c.type == CustomerType.international
    elif customer_type == "local":
        type_filter = or_(
            customer.c.type == CustomerType.local,
            customer.c.type == None
        )

    # Get daily counts
    daily_query = (
        select(
            cast(customer.c.created_at, Date).label('date'),
            func.count().label('count')
        )
        .where(
            and_(
                cast(customer.c.created_at, Date) >= start_date,
                cast(customer.c.created_at, Date) <= end_date
            )
        )
        .group_by(cast(customer.c.created_at, Date))
        .order_by(cast(customer.c.created_at, Date))
    )

    if type_filter is not None:
        daily_query = daily_query.where(type_filter)

    result = await session.execute(daily_query)
    daily_data = result.fetchall()

    # Create a dict for easy lookup
    daily_dict = {row.date: row.count for row in daily_data}

    # Fill in missing dates with 0
    daily_breakdown = []
    current_date = start_date
    while current_date <= end_date:
        count = daily_dict.get(current_date, 0)
        daily_breakdown.append(DailySalesResponse(
            date=current_date.isoformat(),
            count=count
        ))
        current_date += timedelta(days=1)

    # Also get summary stats
    dates = get_date_ranges()

    # Today
    today_query = select(func.count()).select_from(customer).where(
        cast(customer.c.created_at, Date) == dates["today"]
    )
    if type_filter is not None:
        today_query = today_query.where(type_filter)
    today_result = await session.execute(today_query)
    today_count = today_result.scalar() or 0

    # Yesterday
    yesterday_query = select(func.count()).select_from(customer).where(
        cast(customer.c.created_at, Date) == dates["yesterday"]
    )
    if type_filter is not None:
        yesterday_query = yesterday_query.where(type_filter)
    yesterday_result = await session.execute(yesterday_query)
    yesterday_count = yesterday_result.scalar() or 0

    # This week
    this_week_query = select(func.count()).select_from(customer).where(
        and_(
            cast(customer.c.created_at, Date) >= dates["this_week_start"],
            cast(customer.c.created_at, Date) <= dates["this_week_end"]
        )
    )
    if type_filter is not None:
        this_week_query = this_week_query.where(type_filter)
    this_week_result = await session.execute(this_week_query)
    this_week_count = this_week_result.scalar() or 0

    # Last week
    last_week_query = select(func.count()).select_from(customer).where(
        and_(
            cast(customer.c.created_at, Date) >= dates["last_week_start"],
            cast(customer.c.created_at, Date) <= dates["last_week_end"]
        )
    )
    if type_filter is not None:
        last_week_query = last_week_query.where(type_filter)
    last_week_result = await session.execute(last_week_query)
    last_week_count = last_week_result.scalar() or 0

    summary = SalesStatsResponse(
        today=today_count,
        yesterday=yesterday_count,
        this_week=this_week_count,
        last_week=last_week_count,
        customer_type=customer_type
    )

    return DetailedSalesResponse(
        summary=summary,
        daily_breakdown=daily_breakdown,
        date_range=f"{start_date.isoformat()} to {end_date.isoformat()}"
    )


@router.get("/international", response_model=List[dict], summary="International leadlar ro'yxati")
async def get_international_leads(
    limit: int = Query(50, ge=1, le=500, description="Nechta lead qaytarish"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_sales_access)
):
    """
    Faqat international leadlar ro'yxati
    International sales page uchun
    """
    from utils.crypto import decrypt_text

    result = await session.execute(
        select(customer)
        .where(customer.c.type == CustomerType.international)
        .order_by(customer.c.created_at.desc())
        .limit(limit)
    )
    customers = result.fetchall()

    return [
        {
            "id": c.id,
            "full_name": decrypt_text(c.full_name),
            "platform": c.platform,
            "username": c.username,
            "phone_number": decrypt_text(c.phone_number),
            "status": c.status.value if hasattr(c.status, 'value') else str(c.status),
            "status_name": c.status_name,
            "type": c.type.value if c.type else "local",
            "assistant_name": c.assistant_name,
            "notes": c.notes,
            "conversation_language": c.conversation_language.value if c.conversation_language else "uz",
            "created_at": c.created_at.isoformat()
        }
        for c in customers
    ]
