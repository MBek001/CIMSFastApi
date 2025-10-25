# routers/finance.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, insert, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, date
from typing import List, Optional
from decimal import Decimal
import calendar

# Import qilinadigan modellar
from models.admin_models import (
    finance, donation_balance, exchange_rate,
    FinanceType, FinanceStatus, CardType, CurrencyType, TransactionStatus
)
from models.user_models import user, user_page_permission, UserRole, PageName, credit_card
from schemes.schemes_finance import (
    FinanceCreateRequest, FinanceUpdateRequest, FinanceResponse, FinanceListResponse,
    TransferRequest, DashboardResponse, SuccessResponse, CreateResponse,
    DonationResetResponse, BalanceInfo, ExchangeRateResponse, CardTopUpRequest
)
from auth_utils.auth_func import get_current_active_user
from database import get_async_session

# Valyuta util'lari
from utils.currency import CurrencyService, get_last_rate_from_db

router = APIRouter(prefix="/finance", tags=['Finance Management'])


# --- DECORATOR: Finance huquqini tekshirish ---
def require_finance_access(current_user=Depends(get_current_active_user)):
    """Finance sahifasiga kirish huquqini tekshirish"""
    if current_user.company_code not in ["ceo", "finance_director"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Faqat CEO yoki Finance Director ushbu amalni bajara oladi"
        )
    return current_user


# --- Hamma joy faqat DB'dagi so‘nggi kursni ishlatishi uchun getter ---
async def get_db_exchange_rate(session: AsyncSession) -> Decimal:
    return await get_last_rate_from_db(session)


# --- Helper: Donation balansini olish ---
async def get_donation_balance(session: AsyncSession) -> Decimal:
    result = await session.execute(select(donation_balance.c.total_donation))
    balance = result.scalar()
    return balance if balance else Decimal('0')


# --- Helper: karta nomini ko'rsatish ---
def get_card_display(card_value: str) -> str:
    card_displays = {
        "card1": "Company Account UZB",
        "card2": "Uzcard UZB",
        "card3": "Company Account US"
    }
    return card_displays.get(card_value, card_value)


# --- Helper: Balans hisoblash (faqat DB kursi bilan) ---
async def calculate_card_balances(session: AsyncSession):
    current_rate = await get_db_exchange_rate(session)

    card1_balance = Decimal('0')  # UZS
    card2_balance = Decimal('0')  # UZS
    card3_balance = Decimal('0')  # USD
    potential_income = Decimal('0')
    potential_outcome = Decimal('0')

    today = datetime.now().date()
    next_30 = today + timedelta(days=30)

    result = await session.execute(select(finance))
    finances_rows = result.fetchall()

    for fin in finances_rows:
        # Donation ni mos valyutaga aylantirish
        if fin.currency == CurrencyType.USD:
            donation_in_currency = fin.donation / fin.exchange_rate
        else:
            donation_in_currency = fin.donation

        # Net amount (sign bilan)
        if fin.type == FinanceType.incomer:
            netto_amount = fin.summ - donation_in_currency
        else:
            netto_amount = -fin.summ

        # UZSga aylantirish (UZS bo‘lsa o‘zini olamiz)
        netto_amount_uzs = netto_amount * current_rate if fin.currency == CurrencyType.USD else netto_amount

        # Real tranzaksiyalar — balansga ta'sir qiladi
        if fin.transaction_status == TransactionStatus.real:
            if fin.card == CardType.card1:
                card1_balance += netto_amount_uzs
            elif fin.card == CardType.card2:
                card2_balance += netto_amount_uzs
            elif fin.card == CardType.card3:
                # USD kartasi balansini USD ko‘rinishida yuritamiz
                card3_balance += netto_amount

        # Statistik tranzaksiyalar — potentsialga ta'sir (keyingi 30 kunda)
        elif fin.transaction_status == TransactionStatus.statistical and today < fin.date <= next_30:
            if fin.type == FinanceType.incomer:
                potential_income += netto_amount_uzs
            else:
                potential_outcome += abs(netto_amount_uzs)

        # Monthly takrorlar
        if fin.status == FinanceStatus.monthly and fin.initial_date:
            repeat_date = fin.initial_date
            while repeat_date <= next_30:
                if repeat_date == today and fin.transaction_status == TransactionStatus.real:
                    if fin.card == CardType.card1:
                        card1_balance += netto_amount_uzs
                    elif fin.card == CardType.card2:
                        card2_balance += netto_amount_uzs
                    elif fin.card == CardType.card3:
                        card3_balance += netto_amount

                if today <= repeat_date <= next_30:
                    if fin.type == FinanceType.incomer:
                        potential_income += netto_amount_uzs
                    else:
                        potential_outcome += abs(netto_amount_uzs)

                # Keyingi oyga o'tkazish
                if repeat_date.month == 12:
                    repeat_date = repeat_date.replace(year=repeat_date.year + 1, month=1)
                else:
                    next_month = repeat_date.month + 1
                    max_day = calendar.monthrange(repeat_date.year, next_month)[1]
                    day = min(repeat_date.day, max_day)
                    repeat_date = repeat_date.replace(month=next_month, day=day)

    total_balance = card1_balance + card2_balance + (card3_balance * current_rate)
    potential_balance = total_balance + potential_income - potential_outcome

    return {
        "card1_balance": card1_balance,
        "card2_balance": card2_balance,
        "card3_balance": card3_balance,
        "total_balance": total_balance,
        "potential_balance": potential_balance,
        "potential_income": potential_income,
        "potential_outcome": potential_outcome
    }


# --- 1. FINANCE DASHBOARD ---
@router.get("/dashboard", response_model=DashboardResponse, summary="Finance Dashboard")
async def finance_dashboard(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    # Barcha finance yozuvlari
    result = await session.execute(select(finance).order_by(finance.c.date.desc()))
    finances_rows = result.fetchall()

    # Permissions
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == current_user.id)
    )
    permissions = [perm.page_name.value for perm in permissions_result.fetchall()]

    page_order = ['ceo', 'payment_list', 'project_toggle', 'crm', 'finance_list']
    modified_permissions = []
    for page in page_order:
        if page in permissions:
            modified_permissions.append({
                'ceo': 'Dashboard',
                'payment_list': 'Payment',
                'project_toggle': 'Wordpress',
                'crm': 'Sales CRM',
                'finance_list': 'Finance'
            }.get(page, page))

    # Balans
    balances = await calculate_card_balances(session)

    # Donation balance
    donation_bal = await get_donation_balance(session)

    # Exchange rate (faqat DB)
    current_rate = await get_db_exchange_rate(session)

    # Members va kartalari
    members_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname).where(user.c.role == UserRole.member)
    )
    members = members_result.fetchall()

    member_data = []
    for m in members:
        cards_result = await session.execute(
            select(credit_card.c.card_number, credit_card.c.is_primary)
            .where(credit_card.c.user_id == m.id, credit_card.c.is_active == True)
        )
        cards = cards_result.fetchall()
        member_data.append({
            "name": m.name,
            "surname": m.surname,
            "cards": [{"card_number": c.card_number, "is_primary": c.is_primary} for c in cards]
        })

    # Finance list
    finance_list = []
    for fin in finances_rows:
        finance_list.append({
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
        })

    return DashboardResponse(
        finances=finance_list,
        permissions=modified_permissions,
        donation_balance=float(donation_bal),
        exchange_rate=f"{current_rate:,.2f}",
        balances=BalanceInfo(
            card1_balance=f"{balances['card1_balance']:,.2f}",
            card2_balance=f"{balances['card2_balance']:,.2f}",
            card3_balance=f"{balances['card3_balance']:,.2f}",
            total_balance=f"{balances['total_balance']:,.2f}",
            potential_balance=f"{balances['potential_balance']:,.2f}"
        ),
        member_data=member_data
    )


# --- 2. FINANCE YARATISH ---
@router.post("/create", response_model=CreateResponse, summary="Yangi finance yozuvi yaratish")
async def create_finance(
        finance_data: FinanceCreateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    current_rate = await get_db_exchange_rate(session)

    # Currency ni card asosida belgilash
    currency = CurrencyType.UZS if finance_data.card in [CardType.card1, CardType.card2] else CurrencyType.USD

    # Donation hisoblash
    donation_amount = Decimal('0')
    if finance_data.type == FinanceType.incomer and finance_data.donation_percentage > 0:
        donation_in_currency = finance_data.summ * (finance_data.donation_percentage / 100)
        donation_amount = donation_in_currency if currency == CurrencyType.UZS else (donation_in_currency * current_rate)

        # Donation balance yangilash
        current_donation = await get_donation_balance(session)
        await session.execute(update(donation_balance).values(total_donation=current_donation + donation_amount))

    # Initial date (monthly)
    initial_date = finance_data.date if finance_data.status == FinanceStatus.monthly else None

    # Yozuv
    finance_dict = {
        "type": finance_data.type,
        "status": finance_data.status,
        "card": finance_data.card,
        "service": finance_data.service,
        "summ": finance_data.summ,
        "currency": currency,
        "date": finance_data.date,
        "donation": donation_amount,
        "donation_percentage": finance_data.donation_percentage,
        "tax_percentage": finance_data.tax_percentage,
        "exchange_rate": current_rate,
        "transaction_status": finance_data.transaction_status,
        "initial_date": initial_date
    }

    result = await session.execute(insert(finance).values(**finance_dict))
    await session.commit()

    return CreateResponse(message="Finance yozuvi muvaffaqiyatli yaratildi", id=result.inserted_primary_key[0])


# --- 3. FINANCE YANGILASH ---
@router.put("/{finance_id}", response_model=SuccessResponse, summary="Finance yozuvini yangilash")
async def update_finance(
        finance_id: int,
        finance_data: FinanceUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    existing_result = await session.execute(select(finance).where(finance.c.id == finance_id))
    existing_finance = existing_result.fetchone()
    if not existing_finance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance yozuvi topilmadi")

    current_rate = await get_db_exchange_rate(session)

    # Eski donation ni kamaytirib qo'yamiz
    if existing_finance.donation:
        current_donation = await get_donation_balance(session)
        await session.execute(update(donation_balance).values(total_donation=current_donation - existing_finance.donation))

    # Currency
    currency = CurrencyType.UZS if finance_data.card in [CardType.card1, CardType.card2] else CurrencyType.USD

    # Yangi donation
    donation_amount = Decimal('0')
    if finance_data.type == FinanceType.incomer and finance_data.donation_percentage > 0:
        donation_in_currency = finance_data.summ * (finance_data.donation_percentage / 100)
        donation_amount = donation_in_currency if currency == CurrencyType.UZS else (donation_in_currency * current_rate)

        current_donation = await get_donation_balance(session)
        await session.execute(update(donation_balance).values(total_donation=current_donation + donation_amount))

    # Initial date
    initial_date = existing_finance.initial_date
    if finance_data.status == FinanceStatus.monthly and not initial_date:
        initial_date = finance_data.date

    update_data = {
        "type": finance_data.type,
        "status": finance_data.status,
        "card": finance_data.card,
        "service": finance_data.service,
        "summ": finance_data.summ,
        "currency": currency,
        "date": finance_data.date,
        "donation": donation_amount,
        "donation_percentage": finance_data.donation_percentage,
        "tax_percentage": finance_data.tax_percentage,
        "exchange_rate": current_rate,
        "transaction_status": finance_data.transaction_status,
        "initial_date": initial_date
    }

    await session.execute(update(finance).where(finance.c.id == finance_id).values(**update_data))
    await session.commit()
    return SuccessResponse(message="Finance yozuvi muvaffaqiyatli yangilandi")


# --- 4. FINANCE O'CHIRISH ---
@router.delete("/{finance_id}", response_model=SuccessResponse, summary="Finance yozuvini o'chirish")
async def delete_finance(
        finance_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    existing_result = await session.execute(select(finance).where(finance.c.id == finance_id))
    existing_finance = existing_result.fetchone()
    if not existing_finance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance yozuvi topilmadi")

    if existing_finance.donation:
        current_donation = await get_donation_balance(session)
        await session.execute(update(donation_balance).values(total_donation=current_donation - existing_finance.donation))

    await session.execute(delete(finance).where(finance.c.id == finance_id))
    await session.commit()
    return SuccessResponse(message="Finance yozuvi muvaffaqiyatli o'chirildi")


# --- 5. TRANSFER ---
@router.post("/transfer", response_model=SuccessResponse, summary="Kartalar o'rtasida transfer")
async def finance_transfer(
        transfer_data: TransferRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    if transfer_data.from_card == transfer_data.to_card:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bir xil kartaga o'tkazib bo'lmaydi")
    if transfer_data.amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transfer summasi 0 dan katta bo'lishi kerak")

    current_rate = await get_db_exchange_rate(session)

    from_currency = CurrencyType.UZS if transfer_data.from_card in [CardType.card1, CardType.card2] else CurrencyType.USD
    tax_amount = transfer_data.amount * (transfer_data.tax_percentage / 100)
    net_amount = transfer_data.amount - tax_amount
    today = datetime.now().date()

    # From card (outcome)
    from_finance_dict = {
        "type": FinanceType.outcomer,
        "status": FinanceStatus.one_time,
        "card": transfer_data.from_card,
        "service": f"Transfer to {transfer_data.to_card.value}",
        "summ": transfer_data.amount,
        "currency": from_currency,
        "date": today,
        "donation": Decimal('0'),
        "donation_percentage": Decimal('0'),
        "tax_percentage": transfer_data.tax_percentage,
        "exchange_rate": current_rate,
        "transaction_status": TransactionStatus.real
    }
    await session.execute(insert(finance).values(**from_finance_dict))

    # To card currency
    to_currency = CurrencyType.UZS if transfer_data.to_card in [CardType.card1, CardType.card2] else CurrencyType.USD

    # Convert
    if transfer_data.from_card == CardType.card3 and transfer_data.to_card in [CardType.card1, CardType.card2]:
        net_amount_converted = net_amount * current_rate
    elif transfer_data.from_card in [CardType.card1, CardType.card2] and transfer_data.to_card == CardType.card3:
        net_amount_converted = net_amount / current_rate
    else:
        net_amount_converted = net_amount

    # To card (income)
    to_finance_dict = {
        "type": FinanceType.incomer,
        "status": FinanceStatus.one_time,
        "card": transfer_data.to_card,
        "service": f"Transfer from {transfer_data.from_card.value}",
        "summ": net_amount_converted,
        "currency": to_currency,
        "date": today,
        "donation": Decimal('0'),
        "donation_percentage": Decimal('0'),
        "tax_percentage": transfer_data.tax_percentage,
        "exchange_rate": current_rate,
        "transaction_status": TransactionStatus.real
    }
    await session.execute(insert(finance).values(**to_finance_dict))
    await session.commit()

    return SuccessResponse(
        message=f"Transfer OK. From: {transfer_data.from_card.value}, To: {transfer_data.to_card.value}, Amount: {transfer_data.amount}, Net: {net_amount_converted:.2f}"
    )


# --- 6. DONATION BALANCE RESET ---
@router.post("/reset-donation", response_model=DonationResetResponse, summary="Donation balansini reset qilish")
async def reset_donation_balance(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    if current_user.company_code not in ["ceo", "finance_director"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Faqat CEO yoki Finance Director donation balansini reset qila oladi")

    await session.execute(update(donation_balance).values(total_donation=Decimal('0')))
    await session.commit()

    return DonationResetResponse(success=True, message="Donation balansi 0 qilindi", new_balance=0.0)


# --- 7. EXCHANGE RATE (DB'dan ko'rish) ---
@router.get("/exchange-rate", response_model=ExchangeRateResponse, summary="Joriy valyuta kursini olish (DB)")
async def get_exchange_rate(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    current_rate = await get_db_exchange_rate(session)
    return ExchangeRateResponse(usd_to_uzs=float(current_rate), formatted_rate=f"{current_rate:,.2f}")


# --- 8. EXCHANGE RATE (LIVE API + DBga yozish) ---
@router.get("/exchange-rate/live", response_model=ExchangeRateResponse, summary="USD→UZS jonli kurs (API)")
async def get_live_exchange_rate(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_finance_access),
):
    """
    Tashqi API dan kursni olib (CurrencyFreaks), DBga yozadi va javob qaytaradi.
    """
    service = CurrencyService()
    live = await service.fetch_usd_to_uzs()
    await service.write_rate_to_db(session, live)
    return ExchangeRateResponse(usd_to_uzs=float(live), formatted_rate=f"{live:,.2f}")


@router.post("/exchange-rate/sync", response_model=SuccessResponse, summary="Kursni majburan API dan yangilash")
async def sync_exchange_rate(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_finance_access),
):
    """
    Majburan CurrencyFreaks'dan kursni olib DBga yozadi (keshni yangilaydi).
    """
    service = CurrencyService()
    live = await service.fetch_usd_to_uzs()
    await service.write_rate_to_db(session, live)
    return SuccessResponse(message=f"Kurs yangilandi: 1 USD = {live:,.2f} UZS")


# --- 9. FINANCE YOZUVINI OLISH ---
@router.get("/{finance_id}", response_model=FinanceResponse, summary="Finance yozuvini olish")
async def get_finance_item(
        finance_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    result = await session.execute(select(finance).where(finance.c.id == finance_id))
    finance_data = result.fetchone()
    if not finance_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance yozuvi topilmadi")

    return FinanceResponse(
        id=finance_data.id,
        type=finance_data.type.value,
        status=finance_data.status.value,
        card=finance_data.card.value,
        card_display=get_card_display(finance_data.card.value),
        service=finance_data.service,
        summ=float(finance_data.summ),
        currency=finance_data.currency.value,
        date=finance_data.date.isoformat(),
        donation=float(finance_data.donation),
        donation_percentage=float(finance_data.donation_percentage),
        tax_percentage=float(finance_data.tax_percentage) if finance_data.tax_percentage else 0,
        exchange_rate=float(finance_data.exchange_rate) if finance_data.exchange_rate else 0,
        transaction_status=finance_data.transaction_status.value,
        initial_date=finance_data.initial_date.isoformat() if finance_data.initial_date else None
    )


# --- 10. FINANCE LIST (Pagination) ---
@router.get("/", response_model=FinanceListResponse, summary="Finance yozuvlari ro'yxati")
async def get_finance_list(
        page: int = 1,
        per_page: int = 10,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    offset = (page - 1) * per_page
    count_result = await session.execute(select(func.count(finance.c.id)))
    total_count = count_result.scalar()

    result = await session.execute(
        select(finance).order_by(finance.c.date.desc()).limit(per_page).offset(offset)
    )
    rows = result.fetchall()

    finance_list = []
    for fin in rows:
        finance_list.append({
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
        })

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


# --- 11. TOP UP ---
@router.post("/topup", response_model=SuccessResponse, summary="Kartaga pul qo'shish (Top Up)")
async def topup_card(
    data: CardTopUpRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_finance_access),
):
    current_rate = await get_db_exchange_rate(session)
    today = datetime.now().date()

    currency = CurrencyType.UZS if data.card in [CardType.card1, CardType.card2] else CurrencyType.USD

    donation_amount = Decimal('0')
    if data.donation_percentage and data.donation_percentage > 0:
        donation_in_currency = data.amount * (data.donation_percentage / 100)
        donation_amount = donation_in_currency if currency == CurrencyType.UZS else (donation_in_currency * current_rate)

        current_donation = await get_donation_balance(session)
        await session.execute(update(donation_balance).values(total_donation=current_donation + donation_amount))

    fin_row = {
        "type": FinanceType.incomer,
        "status": FinanceStatus.one_time,
        "card": data.card,
        "service": "Top Up",
        "summ": data.amount,
        "currency": currency,
        "date": today,
        "donation": donation_amount,
        "donation_percentage": data.donation_percentage or Decimal('0'),
        "tax_percentage": Decimal('0'),
        "exchange_rate": current_rate,
        "transaction_status": data.transaction_status,
        "initial_date": None
    }

    await session.execute(insert(finance).values(**fin_row))
    await session.commit()

    return SuccessResponse(message=f"Top Up OK: {data.card.value} +{data.amount}")
