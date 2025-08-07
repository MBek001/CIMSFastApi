# routers/finance_advanced.py - Qo'shimcha Finance API endpointlari

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func, and_, or_, extract
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, date
from typing import List, Optional
from decimal import Decimal

from models.admin_models import finance, FinanceType, FinanceStatus, CardType, TransactionStatus
from schemes.schemes_finance import (
    FinanceStatsResponse, FinanceFilterRequest, MonthlyFinanceReport,
    YearlyFinanceReport, FinanceListResponse, FinanceResponse
)
from auth_utils.auth_func import get_current_active_user
from database import get_async_session

advanced_router = APIRouter(prefix="/finance/advanced", tags=['Finance Advanced'])


def require_finance_access(current_user=Depends(get_current_active_user)):
    """Finance sahifasiga kirish huquqini tekshirish"""
    if current_user.company_code not in ["ceo", "finance_director"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Faqat CEO yoki Finance Director ushbu amalni bajara oladi"
        )
    return current_user


# --- 1. FINANCE STATISTIKA ---
@advanced_router.get("/stats", response_model=FinanceStatsResponse, summary="Finance statistikasi")
async def get_finance_stats(
        date_from: Optional[date] = Query(None, description="Boshlanish sanasi"),
        date_to: Optional[date] = Query(None, description="Tugash sanasi"),
        card: Optional[CardType] = Query(None, description="Muayyan karta"),
        transaction_status: Optional[TransactionStatus] = Query(None, description="Tranzaksiya holati"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Finance statistikasini olish (umumiy kirim, chiqim, foyda va h.k.)
    """
    # Base query
    query = select(finance)
    conditions = []

    # Date filter
    if date_from:
        conditions.append(finance.c.date >= date_from)
    if date_to:
        conditions.append(finance.c.date <= date_to)

    # Card filter
    if card:
        conditions.append(finance.c.card == card)

    # Transaction status filter
    if transaction_status:
        conditions.append(finance.c.transaction_status == transaction_status)

    if conditions:
        query = query.where(and_(*conditions))

    result = await session.execute(query)
    finances = result.fetchall()

    # Statistikalarni hisoblash
    total_income = Decimal('0')
    total_outcome = Decimal('0')
    total_donation = Decimal('0')
    income_count = 0
    outcome_count = 0

    for fin in finances:
        if fin.type == FinanceType.incomer:
            total_income += fin.summ
            income_count += 1
        else:  # outcomer
            total_outcome += fin.summ
            outcome_count += 1

        total_donation += fin.donation

    net_profit = total_income - total_outcome
    transaction_count = len(finances)

    return FinanceStatsResponse(
        total_income=float(total_income),
        total_outcome=float(total_outcome),
        total_donation=float(total_donation),
        net_profit=float(net_profit),
        transaction_count=transaction_count,
        income_count=income_count,
        outcome_count=outcome_count
    )


# --- 2. FILTER BILAN FINANCE RO'YXATI ---
@advanced_router.post("/filtered-list", response_model=FinanceListResponse, summary="Filter bilan finance ro'yxati")
async def get_filtered_finance_list(
        filters: FinanceFilterRequest,
        page: int = Query(1, ge=1, description="Sahifa raqami"),
        per_page: int = Query(10, ge=1, le=100, description="Har sahifada nechta"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Filter parametrlari bilan finance yozuvlarini olish
    """
    # Base query
    query = select(finance)
    conditions = []

    # Filter conditions
    if filters.type:
        conditions.append(finance.c.type == filters.type)
    if filters.status:
        conditions.append(finance.c.status == filters.status)
    if filters.card:
        conditions.append(finance.c.card == filters.card)
    if filters.currency:
        conditions.append(finance.c.currency == filters.currency)
    if filters.transaction_status:
        conditions.append(finance.c.transaction_status == filters.transaction_status)
    if filters.date_from:
        conditions.append(finance.c.date >= filters.date_from)
    if filters.date_to:
        conditions.append(finance.c.date <= filters.date_to)
    if filters.service_search:
        conditions.append(finance.c.service.ilike(f"%{filters.service_search}%"))

    if conditions:
        query = query.where(and_(*conditions))

    # Count query
    count_query = select(func.count()).select_from(query.alias())
    count_result = await session.execute(count_query)
    total_count = count_result.scalar()

    # Offset calculation
    offset = (page - 1) * per_page

    # Main query with pagination
    result = await session.execute(
        query.order_by(finance.c.date.desc()).limit(per_page).offset(offset)
    )
    finances = result.fetchall()

    # Response data
    finance_list = []
    for fin in finances:
        finance_dict = {
            "id": fin.id,
            "type": fin.type.value,
            "status": fin.status.value,
            "card": fin.card.value,
            "card_display": get_card_display(fin.card.value),
            "service": fin.service,
            "summ": float(fin.summ),
            "currency": fin.currency.value,
            "date": fin.date.isoformat(),
            "donation": float(fin.donation),
            "donation_percentage": float(fin.donation_percentage),
            "tax_percentage": float(fin.tax_percentage) if fin.tax_percentage else 0,
            "exchange_rate": float(fin.exchange_rate) if fin.exchange_rate else 0,
            "transaction_status": fin.transaction_status.value,
            "initial_date": fin.initial_date.isoformat() if fin.initial_date else None
        }
        finance_list.append(finance_dict)

    # Pagination info
    total_pages = (total_count + per_page - 1) // per_page

    return FinanceListResponse(
        finances=finance_list,
        total_count=total_count,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )


# --- 3. OYLIK HISOBOT ---
@advanced_router.get("/monthly-report/{year}/{month}", response_model=MonthlyFinanceReport,
                     summary="Oylik finance hisoboti")
async def get_monthly_report(
        year: int,
        month: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Muayyan oy uchun finance hisoboti
    """
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Oy 1 dan 12 gacha bo'lishi kerak"
        )

    # Query for specific month and year
    result = await session.execute(
        select(finance).where(
            and_(
                extract('year', finance.c.date) == year,
                extract('month', finance.c.date) == month
            )
        )
    )
    finances = result.fetchall()

    # Calculate monthly stats
    total_income = Decimal('0')
    total_outcome = Decimal('0')
    donation_amount = Decimal('0')
    transaction_count = len(finances)

    for fin in finances:
        if fin.type == FinanceType.incomer:
            total_income += fin.summ
        else:
            total_outcome += fin.summ

        donation_amount += fin.donation

    net_amount = total_income - total_outcome

    return MonthlyFinanceReport(
        month=month,
        year=year,
        total_income=float(total_income),
        total_outcome=float(total_outcome),
        net_amount=float(net_amount),
        donation_amount=float(donation_amount),
        transaction_count=transaction_count
    )


# --- 4. YILLIK HISOBOT ---
@advanced_router.get("/yearly-report/{year}", response_model=YearlyFinanceReport, summary="Yillik finance hisoboti")
async def get_yearly_report(
        year: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Muayyan yil uchun to'liq finance hisoboti (oylik taqsimot bilan)
    """
    # Get all finances for the year
    result = await session.execute(
        select(finance).where(extract('year', finance.c.date) == year)
    )
    finances = result.fetchall()

    # Group by months
    monthly_data = {}
    for i in range(1, 13):
        monthly_data[i] = {
            'income': Decimal('0'),
            'outcome': Decimal('0'),
            'donation': Decimal('0'),
            'count': 0
        }

    for fin in finances:
        month = fin.date.month
        monthly_data[month]['count'] += 1
        monthly_data[month]['donation'] += fin.donation

        if fin.type == FinanceType.incomer:
            monthly_data[month]['income'] += fin.summ
        else:
            monthly_data[month]['outcome'] += fin.summ

    # Create monthly reports
    monthly_reports = []
    yearly_total_income = Decimal('0')
    yearly_total_outcome = Decimal('0')
    yearly_donation_amount = Decimal('0')
    yearly_transaction_count = 0

    for month in range(1, 13):
        data = monthly_data[month]
        net_amount = data['income'] - data['outcome']

        monthly_report = MonthlyFinanceReport(
            month=month,
            year=year,
            total_income=float(data['income']),
            total_outcome=float(data['outcome']),
            net_amount=float(net_amount),
            donation_amount=float(data['donation']),
            transaction_count=data['count']
        )
        monthly_reports.append(monthly_report)

        # Add to yearly totals
        yearly_total_income += data['income']
        yearly_total_outcome += data['outcome']
        yearly_donation_amount += data['donation']
        yearly_transaction_count += data['count']

    yearly_net_amount = yearly_total_income - yearly_total_outcome

    return YearlyFinanceReport(
        year=year,
        monthly_reports=monthly_reports,
        yearly_total_income=float(yearly_total_income),
        yearly_total_outcome=float(yearly_total_outcome),
        yearly_net_amount=float(yearly_net_amount),
        yearly_donation_amount=float(yearly_donation_amount),
        yearly_transaction_count=yearly_transaction_count
    )


# --- 5. ENG KO'P ISHLATILADIGAN XIZMATLAR ---
@advanced_router.get("/top-services", summary="Eng ko'p ishlatiladigan xizmatlar")
async def get_top_services(
        limit: int = Query(10, ge=1, le=50, description="Nechta xizmat ko'rsatish"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Eng ko'p ishlatiladigan xizmatlar ro'yxati
    """
    result = await session.execute(
        select(
            finance.c.service,
            func.count(finance.c.id).label('usage_count'),
            func.sum(finance.c.summ).label('total_amount')
        )
        .group_by(finance.c.service)
        .order_by(func.count(finance.c.id).desc())
        .limit(limit)
    )

    services = result.fetchall()

    service_list = []
    for service in services:
        service_dict = {
            "service_name": service.service,
            "usage_count": service.usage_count,
            "total_amount": float(service.total_amount)
        }
        service_list.append(service_dict)

    return {
        "top_services": service_list,
        "total_services_found": len(service_list)
    }


# --- 6. KARTA BO'YICHA STATISTIKA ---
@advanced_router.get("/card-stats", summary="Kartalar bo'yicha statistika")
async def get_card_statistics(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Har bir karta uchun alohida statistika
    """
    result = await session.execute(
        select(
            finance.c.card,
            finance.c.type,
            func.count(finance.c.id).label('transaction_count'),
            func.sum(finance.c.summ).label('total_amount')
        )
        .group_by(finance.c.card, finance.c.type)
    )

    card_stats = result.fetchall()

    # Organize by card
    cards_data = {}
    for stat in card_stats:
        card_name = stat.card.value
        if card_name not in cards_data:
            cards_data[card_name] = {
                "card_name": card_name,
                "card_display": get_card_display(card_name),
                "income_count": 0,
                "income_amount": 0,
                "outcome_count": 0,
                "outcome_amount": 0,
                "total_transactions": 0
            }

        if stat.type == FinanceType.incomer:
            cards_data[card_name]["income_count"] = stat.transaction_count
            cards_data[card_name]["income_amount"] = float(stat.total_amount)
        else:
            cards_data[card_name]["outcome_count"] = stat.transaction_count
            cards_data[card_name]["outcome_amount"] = float(stat.total_amount)

        cards_data[card_name]["total_transactions"] += stat.transaction_count

    return {
        "card_statistics": list(cards_data.values()),
        "total_cards": len(cards_data)
    }


def get_card_display(card_value: str) -> str:
    """Karta nomini ko'rsatish uchun helper function"""
    card_displays = {
        "card1": "Company Account UZB",
        "card2": "Uzcard UZB",
        "card3": "Company Account US"
    }
    return card_displays.get(card_value, card_value)