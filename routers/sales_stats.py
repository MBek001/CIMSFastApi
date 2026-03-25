"""
Sales Statistics Router
Lead tracking with date filters and customer type filtering
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, cast, Date, String
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


class DashboardPeriodResponse(BaseModel):
    start_date: str
    end_date: str
    days: int


class DashboardSummaryResponse(BaseModel):
    total_period_leads: int
    today: int
    yesterday: int
    this_week: int
    last_week: int
    project_started: int
    finished: int
    rejected: int
    conversion_rate_percent: float


class DashboardDistributionItem(BaseModel):
    key: str
    label: str
    value: int
    percentage: float


class DashboardTrendPoint(BaseModel):
    date: str
    count: int


class DashboardChartsResponse(BaseModel):
    customer_type: Optional[str] = None
    period: DashboardPeriodResponse
    summary: DashboardSummaryResponse
    trend: List[DashboardTrendPoint]
    status_distribution: List[DashboardDistributionItem]
    platform_distribution: List[DashboardDistributionItem]
    customer_type_distribution: List[DashboardDistributionItem]


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
            (user_page_permission.c.page_name == PageName.crm.value)
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


def build_customer_type_filter(customer_type: Optional[str]):
    if customer_type is None:
        return None

    normalized = customer_type.strip().lower()
    if normalized == "international":
        return customer.c.type == CustomerType.international
    if normalized == "local":
        return or_(
            customer.c.type == CustomerType.local,
            customer.c.type == None  # noqa: E711
        )

    raise HTTPException(
        status_code=400,
        detail="customer_type faqat 'international' yoki 'local' bo'lishi mumkin"
    )


async def count_customers(
    session: AsyncSession,
    *conditions,
):
    query = select(func.count()).select_from(customer)
    if conditions:
        query = query.where(and_(*conditions))
    result = await session.execute(query)
    return int(result.scalar() or 0)


def make_distribution_items(
    raw_items: List[tuple[str, int]],
    total: int,
) -> List[DashboardDistributionItem]:
    items: List[DashboardDistributionItem] = []
    for key, value in raw_items:
        normalized_key = str(key or "unknown").strip() or "unknown"
        percentage = round((value / total) * 100, 2) if total > 0 else 0.0
        items.append(
            DashboardDistributionItem(
                key=normalized_key,
                label=normalized_key.replace("_", " ").title(),
                value=int(value),
                percentage=percentage,
            )
        )
    return items


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


@router.get("/dashboard/charts", response_model=DashboardChartsResponse, summary="Dashboard uchun chart ma'lumotlari")
async def get_dashboard_charts(
    days: int = Query(30, ge=7, le=365, description="Necha kunlik chart ma'lumotlari kerak"),
    customer_type: Optional[str] = Query(None, description="Customer type filter: 'international' yoki 'local'"),
    platform_limit: int = Query(10, ge=1, le=20, description="Platform chart uchun maksimal platform soni"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_sales_access)
):
    """
    Dashboard chart/grafiklari uchun bitta agregat endpoint.
    Frontend shu endpoint orqali trend va distribution datasetlarni olishi mumkin.
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    type_filter = build_customer_type_filter(customer_type)

    period_conditions = [
        cast(customer.c.created_at, Date) >= start_date,
        cast(customer.c.created_at, Date) <= end_date,
    ]
    if type_filter is not None:
        period_conditions.append(type_filter)

    trend_query = (
        select(
            cast(customer.c.created_at, Date).label("chart_date"),
            func.count().label("count"),
        )
        .where(and_(*period_conditions))
        .group_by(cast(customer.c.created_at, Date))
        .order_by(cast(customer.c.created_at, Date).asc())
    )
    trend_result = await session.execute(trend_query)
    trend_rows = trend_result.fetchall()
    trend_map = {row.chart_date: int(row.count or 0) for row in trend_rows}

    trend: List[DashboardTrendPoint] = []
    current_date = start_date
    while current_date <= end_date:
        trend.append(
            DashboardTrendPoint(
                date=current_date.isoformat(),
                count=trend_map.get(current_date, 0),
            )
        )
        current_date += timedelta(days=1)

    status_expr = func.coalesce(customer.c.status_name, cast(customer.c.status, String))
    status_query = (
        select(
            status_expr.label("status_key"),
            func.count().label("count"),
        )
        .where(and_(*period_conditions))
        .group_by(status_expr)
        .order_by(func.count().desc(), status_expr.asc())
    )
    status_result = await session.execute(status_query)
    status_rows = [(str(row.status_key), int(row.count or 0)) for row in status_result.fetchall()]

    platform_query = (
        select(
            customer.c.platform.label("platform_name"),
            func.count().label("count"),
        )
        .where(and_(*period_conditions))
        .group_by(customer.c.platform)
        .order_by(func.count().desc(), customer.c.platform.asc())
        .limit(platform_limit)
    )
    platform_result = await session.execute(platform_query)
    platform_rows = [
        (str(row.platform_name or "unknown"), int(row.count or 0))
        for row in platform_result.fetchall()
    ]

    type_period_conditions = [
        cast(customer.c.created_at, Date) >= start_date,
        cast(customer.c.created_at, Date) <= end_date,
    ]
    if type_filter is not None:
        type_period_conditions.append(type_filter)

    local_count = await count_customers(
        session,
        *type_period_conditions,
        or_(
            customer.c.type == CustomerType.local,
            customer.c.type == None  # noqa: E711
        ),
    )
    international_count = await count_customers(
        session,
        *type_period_conditions,
        customer.c.type == CustomerType.international,
    )

    total_period_leads = await count_customers(session, *period_conditions)
    dates = get_date_ranges()

    today_conditions = [cast(customer.c.created_at, Date) == dates["today"]]
    yesterday_conditions = [cast(customer.c.created_at, Date) == dates["yesterday"]]
    this_week_conditions = [
        cast(customer.c.created_at, Date) >= dates["this_week_start"],
        cast(customer.c.created_at, Date) <= dates["this_week_end"],
    ]
    last_week_conditions = [
        cast(customer.c.created_at, Date) >= dates["last_week_start"],
        cast(customer.c.created_at, Date) <= dates["last_week_end"],
    ]
    if type_filter is not None:
        today_conditions.append(type_filter)
        yesterday_conditions.append(type_filter)
        this_week_conditions.append(type_filter)
        last_week_conditions.append(type_filter)

    today_count = await count_customers(session, *today_conditions)
    yesterday_count = await count_customers(session, *yesterday_conditions)
    this_week_count = await count_customers(session, *this_week_conditions)
    last_week_count = await count_customers(session, *last_week_conditions)

    status_count_map = {key: value for key, value in status_rows}
    project_started_count = int(status_count_map.get("project_started", 0))
    finished_count = int(status_count_map.get("finished", 0))
    rejected_count = int(status_count_map.get("rejected", 0))
    conversion_rate_percent = round((project_started_count / total_period_leads) * 100, 2) if total_period_leads > 0 else 0.0

    status_distribution = make_distribution_items(status_rows, total_period_leads)
    platform_distribution = make_distribution_items(platform_rows, total_period_leads)
    customer_type_distribution = make_distribution_items(
        [
            ("local", local_count),
            ("international", international_count),
        ],
        total_period_leads,
    )

    return DashboardChartsResponse(
        customer_type=customer_type.strip().lower() if customer_type else None,
        period=DashboardPeriodResponse(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            days=days,
        ),
        summary=DashboardSummaryResponse(
            total_period_leads=total_period_leads,
            today=today_count,
            yesterday=yesterday_count,
            this_week=this_week_count,
            last_week=last_week_count,
            project_started=project_started_count,
            finished=finished_count,
            rejected=rejected_count,
            conversion_rate_percent=conversion_rate_percent,
        ),
        trend=trend,
        status_distribution=status_distribution,
        platform_distribution=platform_distribution,
        customer_type_distribution=customer_type_distribution,
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
            "aisummary": c.aisummary,
            "conversation_language": c.conversation_language.value if c.conversation_language else "uz",
            "created_at": c.created_at.isoformat()
        }
        for c in customers
    ]
