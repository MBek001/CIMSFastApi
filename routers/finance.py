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
    DonationResetResponse, BalanceInfo, ExchangeRateResponse
)
from auth_utils.auth_func import get_current_active_user
from database import get_async_session

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


# --- HELPER FUNCTIONS ---
async def get_current_exchange_rate(session: AsyncSession) -> Decimal:
    """Joriy valyuta kursini olish"""
    result = await session.execute(
        select(exchange_rate.c.usd_to_uzs)
        .order_by(exchange_rate.c.updated_at.desc())
        .limit(1)
    )
    rate = result.scalar()
    return rate if rate else Decimal('12700.00')


async def get_donation_balance(session: AsyncSession) -> Decimal:
    """Donation balansini olish"""
    result = await session.execute(select(donation_balance.c.total_donation))
    balance = result.scalar()
    return balance if balance else Decimal('0')


async def calculate_card_balances(session: AsyncSession):
    """Karta balanslarini hisoblash"""
    current_rate = await get_current_exchange_rate(session)

    card1_balance = Decimal('0')  # Company Account UZB (UZS)
    card2_balance = Decimal('0')  # Uzcard UZB (UZS)
    card3_balance = Decimal('0')  # Company Account US (USD)
    potential_income = Decimal('0')
    potential_outcome = Decimal('0')

    today = datetime.now().date()
    next_30 = today + timedelta(days=30)

    # Barcha finance yozuvlarini olish
    result = await session.execute(select(finance))
    finances = result.fetchall()

    for fin in finances:
        # Donation ni mos valyutaga aylantirish
        donation_in_currency = fin.donation
        if fin.currency == CurrencyType.USD:
            donation_in_currency = fin.donation / fin.exchange_rate
        else:
            donation_in_currency = fin.donation

        # Net amount hisoblash
        if fin.type == FinanceType.incomer:
            netto_amount = fin.summ - donation_in_currency
        else:  # outcomer
            netto_amount = -fin.summ

        netto_amount_uzs = netto_amount * current_rate if fin.currency == CurrencyType.USD else netto_amount

        # Real tranzaksiyalar balansga ta'sir qiladi
        if fin.transaction_status == TransactionStatus.real:
            if fin.card == CardType.card1:
                card1_balance += netto_amount_uzs
            elif fin.card == CardType.card2:
                card2_balance += netto_amount_uzs
            elif fin.card == CardType.card3:
                card3_balance += netto_amount

        # Statistik tranzaksiyalar potential balansga ta'sir qiladi
        elif fin.transaction_status == TransactionStatus.statistical and today < fin.date <= next_30:
            if fin.type == FinanceType.incomer:
                potential_income += netto_amount_uzs
            else:
                potential_outcome += abs(netto_amount_uzs)

        # Monthly tranzaksiyalar
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

                # Bir oy qo'shish
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
    """
    Finance dashboard - barcha finance ma'lumotlari va balanslar
    """
    # Barcha finance yozuvlarini olish
    result = await session.execute(
        select(finance).order_by(finance.c.date.desc())
    )
    finances = result.fetchall()

    # User permissions olish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == current_user.id)
    )
    permissions = [perm.page_name.value for perm in permissions_result.fetchall()]

    # Permission nomlarini o'zgartirish
    page_order = ['ceo', 'payment_list', 'project_toggle', 'crm', 'finance_list']
    modified_permissions = []
    for page in page_order:
        if page in permissions:
            if page == 'ceo':
                modified_permissions.append('Dashboard')
            elif page == 'payment_list':
                modified_permissions.append('Payment')
            elif page == 'project_toggle':
                modified_permissions.append('Wordpress')
            elif page == 'crm':
                modified_permissions.append('Sales CRM')
            elif page == 'finance_list':
                modified_permissions.append('Finance')
            else:
                modified_permissions.append(page)

    # Balanslarni hisoblash
    balances = await calculate_card_balances(session)

    # Donation balance
    donation_bal = await get_donation_balance(session)

    # Exchange rate
    current_rate = await get_current_exchange_rate(session)

    # Member credit cards
    members_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname)
        .where(user.c.role == UserRole.member)
    )
    members = members_result.fetchall()

    member_data = []
    for member in members:
        cards_result = await session.execute(
            select(credit_card.c.card_number, credit_card.c.is_primary)
            .where(credit_card.c.user_id == member.id, credit_card.c.is_active == True)
        )
        cards = cards_result.fetchall()

        member_data.append({
            "name": member.name,
            "surname": member.surname,
            "cards": [{"card_number": card.card_number, "is_primary": card.is_primary} for card in cards]
        })

    # Finance list yaratish
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


def get_card_display(card_value: str) -> str:
    """Karta nomini ko'rsatish uchun"""
    card_displays = {
        "card1": "Company Account UZB",
        "card2": "Uzcard UZB",
        "card3": "Company Account US"
    }
    return card_displays.get(card_value, card_value)


# --- 2. FINANCE YARATISH ---
@router.post("/create", response_model=CreateResponse, summary="Yangi finance yozuvi yaratish")
async def create_finance(
        finance_data: FinanceCreateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Yangi finance yozuvi yaratish
    """
    # Exchange rate olish
    current_rate = await get_current_exchange_rate(session)

    # Currency ni card asosida belgilash
    currency = CurrencyType.UZS
    if finance_data.card in [CardType.card1, CardType.card2]:
        currency = CurrencyType.UZS
    elif finance_data.card == CardType.card3:
        currency = CurrencyType.USD

    # Donation hisoblash
    donation_amount = Decimal('0')
    if finance_data.type == FinanceType.incomer and finance_data.donation_percentage > 0:
        donation_in_currency = finance_data.summ * (finance_data.donation_percentage / 100)
        if currency == CurrencyType.USD:
            donation_amount = donation_in_currency * current_rate
        else:
            donation_amount = donation_in_currency

        # Donation balance yangilash
        current_donation = await get_donation_balance(session)
        await session.execute(
            update(donation_balance).values(total_donation=current_donation + donation_amount)
        )

    # Initial date belgilash (monthly uchun)
    initial_date = None
    if finance_data.status == FinanceStatus.monthly:
        initial_date = finance_data.date

    # Finance yaratish
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

    return CreateResponse(
        message="Finance yozuvi muvaffaqiyatli yaratildi",
        id=result.inserted_primary_key[0]
    )


# --- 3. FINANCE YANGILASH ---
@router.put("/{finance_id}", response_model=SuccessResponse, summary="Finance yozuvini yangilash")
async def update_finance(
        finance_id: int,
        finance_data: FinanceUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Mavjud finance yozuvini yangilash
    """
    # Finance mavjudligini tekshirish
    existing_result = await session.execute(
        select(finance).where(finance.c.id == finance_id)
    )
    existing_finance = existing_result.fetchone()

    if not existing_finance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finance yozuvi topilmadi"
        )

    # Exchange rate olish
    current_rate = await get_current_exchange_rate(session)

    # Eski donation ni donation balance dan ayirish
    if existing_finance.donation:
        current_donation = await get_donation_balance(session)
        await session.execute(
            update(donation_balance).values(total_donation=current_donation - existing_finance.donation)
        )

    # Currency belgilash
    currency = CurrencyType.UZS
    if finance_data.card in [CardType.card1, CardType.card2]:
        currency = CurrencyType.UZS
    elif finance_data.card == CardType.card3:
        currency = CurrencyType.USD

    # Yangi donation hisoblash
    donation_amount = Decimal('0')
    if finance_data.type == FinanceType.incomer and finance_data.donation_percentage > 0:
        donation_in_currency = finance_data.summ * (finance_data.donation_percentage / 100)
        if currency == CurrencyType.USD:
            donation_amount = donation_in_currency * current_rate
        else:
            donation_amount = donation_in_currency

        # Donation balance yangilash
        current_donation = await get_donation_balance(session)
        await session.execute(
            update(donation_balance).values(total_donation=current_donation + donation_amount)
        )

    # Initial date belgilash
    initial_date = existing_finance.initial_date
    if finance_data.status == FinanceStatus.monthly and not initial_date:
        initial_date = finance_data.date

    # Yangilanadigan ma'lumotlar
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

    await session.execute(
        update(finance).where(finance.c.id == finance_id).values(**update_data)
    )
    await session.commit()

    return SuccessResponse(message="Finance yozuvi muvaffaqiyatli yangilandi")


# --- 4. FINANCE O'CHIRISH ---
@router.delete("/{finance_id}", response_model=SuccessResponse, summary="Finance yozuvini o'chirish")
async def delete_finance(
        finance_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Finance yozuvini o'chirish
    """
    # Finance mavjudligini tekshirish
    existing_result = await session.execute(
        select(finance).where(finance.c.id == finance_id)
    )
    existing_finance = existing_result.fetchone()

    if not existing_finance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finance yozuvi topilmadi"
        )

    # Donation balance ni yangilash
    if existing_finance.donation:
        current_donation = await get_donation_balance(session)
        await session.execute(
            update(donation_balance).values(total_donation=current_donation - existing_finance.donation)
        )

    # Finance o'chirish
    await session.execute(delete(finance).where(finance.c.id == finance_id))
    await session.commit()

    return SuccessResponse(message="Finance yozuvi muvaffaqiyatli o'chirildi")


# --- 5. TRANSFER (Kartalar o'rtasida pul o'tkazish) ---
@router.post("/transfer", response_model=SuccessResponse, summary="Kartalar o'rtasida transfer")
async def finance_transfer(
        transfer_data: TransferRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Kartalar o'rtasida pul o'tkazish
    """
    if transfer_data.from_card == transfer_data.to_card:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bir xil kartaga o'tkazib bo'lmaydi"
        )

    if transfer_data.amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transfer summasi 0 dan katta bo'lishi kerak"
        )

    current_rate = await get_current_exchange_rate(session)

    # From card currency
    from_currency = CurrencyType.UZS if transfer_data.from_card in [CardType.card1,
                                                                    CardType.card2] else CurrencyType.USD

    # Tax hisoblash
    tax_amount = transfer_data.amount * (transfer_data.tax_percentage / 100)
    net_amount = transfer_data.amount - tax_amount

    today = datetime.now().date()

    # From card dan chiqarish
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

    from_result = await session.execute(insert(finance).values(**from_finance_dict))

    # To card currency
    to_currency = CurrencyType.UZS if transfer_data.to_card in [CardType.card1, CardType.card2] else CurrencyType.USD

    # Currency conversion
    if transfer_data.from_card == CardType.card3 and transfer_data.to_card in [CardType.card1, CardType.card2]:
        # USD dan UZS ga
        net_amount_converted = net_amount * current_rate
    elif transfer_data.from_card in [CardType.card1, CardType.card2] and transfer_data.to_card == CardType.card3:
        # UZS dan USD ga
        net_amount_converted = net_amount / current_rate
    else:
        # Bir xil valyuta
        net_amount_converted = net_amount

    # To card ga qo'shish
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

    to_result = await session.execute(insert(finance).values(**to_finance_dict))
    await session.commit()

    return SuccessResponse(
        message=f"Transfer muvaffaqiyatli amalga oshirildi. From: {transfer_data.from_card.value}, To: {transfer_data.to_card.value}, Amount: {transfer_data.amount}, Net: {net_amount_converted:.2f}"
    )


# --- 6. DONATION BALANCE RESET ---
@router.post("/reset-donation", response_model=DonationResetResponse, summary="Donation balansini reset qilish")
async def reset_donation_balance(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Donation balansini 0 ga reset qilish (faqat CEO yoki Finance Director)
    """
    if current_user.company_code not in ["ceo", "finance_director"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Faqat CEO yoki Finance Director donation balansini reset qila oladi"
        )

    await session.execute(
        update(donation_balance).values(total_donation=Decimal('0'))
    )
    await session.commit()

    return DonationResetResponse(
        success=True,
        message="Donation balansi muvaffaqiyatli 0 qilindi",
        new_balance=0.0
    )


# --- 7. EXCHANGE RATE OLISH VA YANGILASH ---
@router.get("/exchange-rate", response_model=ExchangeRateResponse, summary="Joriy valyuta kursini olish")
async def get_exchange_rate(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Joriy USD/UZS valyuta kursini olish
    """
    current_rate = await get_current_exchange_rate(session)

    return ExchangeRateResponse(
        usd_to_uzs=float(current_rate),
        formatted_rate=f"{current_rate:,.2f}"
    )


@router.put("/exchange-rate/{new_rate}", response_model=SuccessResponse, summary="Valyuta kursini yangilash")
async def update_exchange_rate(
        new_rate: float,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Valyuta kursini yangilash (faqat CEO)
    """
    if current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Faqat CEO valyuta kursini yangilashi mumkin"
        )

    if new_rate <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valyuta kursi 0 dan katta bo'lishi kerak"
        )

    # Yangi exchange rate yozuvi yaratish
    rate_dict = {
        "usd_to_uzs": Decimal(str(new_rate)),
        "updated_at": datetime.now()
    }

    await session.execute(insert(exchange_rate).values(**rate_dict))
    await session.commit()

    return SuccessResponse(
        message=f"Valyuta kursi muvaffaqiyatli yangilandi: 1 USD = {new_rate:,.2f} UZS"
    )


# --- 8. FINANCE YOZUVINI OLISH ---
@router.get("/{finance_id}", response_model=FinanceResponse, summary="Finance yozuvini olish")
async def get_finance(
        finance_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Bitta finance yozuvining to'liq ma'lumotlarini olish
    """
    result = await session.execute(
        select(finance).where(finance.c.id == finance_id)
    )
    finance_data = result.fetchone()

    if not finance_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finance yozuvi topilmadi"
        )

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


# --- 9. FINANCE LISTINI OLISH (PAGINATION BILAN) ---
@router.get("/", response_model=FinanceListResponse, summary="Finance yozuvlari ro'yxati")
async def get_finance_list(
        page: int = 1,
        per_page: int = 10,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_finance_access)
):
    """
    Finance yozuvlarining sahifalashtirilib olingan ro'yxati
    """
    # Offset hisoblash
    offset = (page - 1) * per_page

    # Umumiy yozuvlar soni
    count_result = await session.execute(select(func.count(finance.c.id)))
    total_count = count_result.scalar()

    # Finance yozuvlarini olish
    result = await session.execute(
        select(finance)
        .order_by(finance.c.date.desc())
        .limit(per_page)
        .offset(offset)
    )
    finances = result.fetchall()

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

    # Sahifalar soni
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