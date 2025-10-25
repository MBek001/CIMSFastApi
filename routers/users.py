from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, insert, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import List

# Import qilinadigan modellar
from models.user_models import user, message, user_payment, user_page_permission, UserRole, PageName
from schemes.schemes_users import (
    UserCreateRequest, UserUpdateRequest, UserResponse, UserListResponse, UserToggleResponse,
    MessageToAllRequest, MessageToUserRequest, MessageListResponse,
    PaymentCreateRequest, PaymentUpdateRequest, PaymentListResponse, PaymentToggleResponse,
    SuccessResponse, CreateResponse, DashboardResponse
)
from auth_utils.auth_func import get_current_active_user, get_password_hash
from database import get_async_session

from  schemes.schemes_users import TodayCustomerInfo,DailyMetricsResponse
from models.user_models import  user_payment
from  models.admin_models import customer,CustomerStatus
from routers.finance import  get_db_exchange_rate,calculate_card_balances

router = APIRouter(prefix="/ceo", tags=['CEO Dashboard'])


# --- DECORATOR: CEO huquqini tekshirish ---
def require_ceo_access(current_user=Depends(get_current_active_user)):
    if current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Faqat CEO ushbu amalni bajara oladi"
        )
    return current_user


# --- 1. CEO DASHBOARD - Barcha userlar ro'yxati ---
@router.get("/dashboard", response_model=DashboardResponse, summary="CEO Dashboard - barcha userlar")
async def ceo_dashboard(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):

    # Barcha userlarni olish
    result = await session.execute(select(user))
    users = result.fetchall()

    # Statistika hisoblash
    user_count = len(users)
    active_user_count = len([u for u in users if u.is_active])
    inactive_user_count = user_count - active_user_count

    # Xabarlar sonini hisoblash
    messages_result = await session.execute(select(func.count(message.c.id)))
    messages_count = messages_result.scalar()

    # Har bir user uchun permissions olish
    users_with_permissions = []
    for user_data in users:
        permissions_result = await session.execute(
            select(user_page_permission.c.page_name)
            .where(user_page_permission.c.user_id == user_data.id)
        )
        permissions = [perm.page_name.value for perm in permissions_result.fetchall()]

        # Permission nomlarini o'zgartirish
        modified_permissions = []
        for perm in permissions:
            if perm == 'ceo':
                modified_permissions.append('Dashboard')
            elif perm == 'payment_list':
                modified_permissions.append('Payment')
            elif perm == 'project_toggle':
                modified_permissions.append('Wordpress')
            elif perm == 'crm':
                modified_permissions.append('Sales CRM')
            elif perm == 'finance_list':
                modified_permissions.append('Finance')
            elif perm == 'update_list':
                modified_permissions.append('Update')
            else:
                modified_permissions.append(perm)

        user_dict = {
            "id": user_data.id,
            "email": user_data.email,
            "name": user_data.name,
            "surname": user_data.surname,
            "company_code": user_data.company_code,
            "telegram_id": user_data.telegram_id,
            "default_salary": float(user_data.default_salary),
            "role": user_data.role.value,
            "is_active": user_data.is_active,
            "permissions": modified_permissions
        }
        users_with_permissions.append(user_dict)

    return DashboardResponse(
        users=users_with_permissions,
        statistics={
            "user_count": user_count,
            "messages_count": messages_count,
            "active_user_count": active_user_count,
            "inactive_user_count": inactive_user_count
        }
    )

@router.get("/metrics/today", response_model=DailyMetricsResponse,
            summary="Bugungi metrikalar: customers, need_to_call count, total balance, due payments today")
async def get_today_metrics(

session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)

):
    """
    - Bugun yaratilgan customerlar ro'yxati (customer.created_at = bugun)
    - 'need_to_call' holatidagi customerlar soni
    - Total Balance (card1 + card2 + card3 * USD->UZS), faqat DB'dagi eng so'nggi kurs bilan
    - Due payments today: user_payment.date = bugun va payment = false bo'lgan yozuvlar soni
    """
    today = datetime.now().date()

    # 1) Bugungi customerlar
    today_cust_res = await session.execute(
        select(
            customer.c.id,
            customer.c.full_name,
            customer.c.platform,
            customer.c.username,
            customer.c.phone_number,
            customer.c.status,
            customer.c.assistant_name,
            customer.c.created_at,
        ).where(func.date(customer.c.created_at) == today)
         .order_by(customer.c.created_at.desc())
    )
    today_customers_rows = today_cust_res.fetchall()

    today_customers: list[TodayCustomerInfo] = []
    for row in today_customers_rows:
        today_customers.append(
            TodayCustomerInfo(
                id=row.id,
                full_name=row.full_name,
                platform=row.platform,
                username=row.username,
                phone_number=row.phone_number,
                status=row.status.value if hasattr(row.status, "value") else str(row.status),
                assistant_name=row.assistant_name,
                created_at=row.created_at.isoformat() if row.created_at else None,
            )
        )

    # 2) need_to_call holati soni
    need_to_call_res = await session.execute(
        select(func.count()).where(customer.c.status == CustomerStatus.need_to_call)
    )
    need_to_call_count = int(need_to_call_res.scalar() or 0)

    # 3) Total balance (faqat DB kursi bilan)
    current_rate = await get_db_exchange_rate(session)
    balances = await calculate_card_balances(session)
    total_balance_uzs = balances["card1_balance"] + balances["card2_balance"] + (balances["card3_balance"] * current_rate)

    # 4) Due payments today (to'lanmagan bugungi to'lovlar)
    due_pay_res = await session.execute(
        select(func.count())
        .where(
            user_payment.c.date == today,
            (user_payment.c.payment == False)  # noqa: E712
        )
    )
    due_payments_today = int(due_pay_res.scalar() or 0)

    return DailyMetricsResponse(
        today_customers=today_customers,
        need_to_call_count=need_to_call_count,
        total_balance_uzs=float(total_balance_uzs),
        total_balance_formatted=f"{total_balance_uzs:,.2f}",
        due_payments_today=due_payments_today,
    )


# --- 2. USER YARATISH ---
@router.post("/users", response_model=CreateResponse, summary="Yangi user yaratish")
async def create_user(
        user_data: UserCreateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Yangi foydalanuvchi yaratish (faqat CEO)
    """
    # Email mavjudligini tekshirish
    existing_user_result = await session.execute(
        select(user).where(user.c.email == user_data.email)
    )
    if existing_user_result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bu email allaqachon mavjud"
        )

    # Parolni hash qilish
    hashed_password = get_password_hash(user_data.password)

    # Yangi user yaratish
    user_dict = {
        "email": user_data.email,
        "name": user_data.name,
        "surname": user_data.surname,
        "password": hashed_password,
        "company_code": user_data.company_code,
        "telegram_id": user_data.telegram_id,
        "default_salary": user_data.default_salary,
        "role": user_data.role,
        "is_active": user_data.is_active
    }

    result = await session.execute(insert(user).values(**user_dict))
    await session.commit()

    return CreateResponse(
        message="Foydalanuvchi muvaffaqiyatli yaratildi",
        id=result.inserted_primary_key[0]
    )


# --- 3. USER YANGILASH ---
@router.put("/users/{user_id}", response_model=SuccessResponse, summary="User ma'lumotlarini yangilash")
async def update_user(
        user_id: int,
        user_data: UserUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Mavjud foydalanuvchi ma'lumotlarini yangilash
    """
    # User mavjudligini tekshirish
    existing_user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    existing_user = existing_user_result.fetchone()

    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # Yangilanadigan ma'lumotlarni tayyorlash
    update_data = {}
    for field, value in user_data.dict(exclude_unset=True).items():
        if field == "password" and value:
            update_data[field] = get_password_hash(value)
        elif value is not None:
            update_data[field] = value

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi"
        )

    # Ma'lumotlarni yangilash
    await session.execute(
        update(user).where(user.c.id == user_id).values(**update_data)
    )
    await session.commit()

    return SuccessResponse(message="Foydalanuvchi muvaffaqiyatli yangilandi")


# --- 4. USER O'CHIRISH ---
@router.delete("/users/{user_id}", response_model=SuccessResponse, summary="User o'chirish")
async def delete_user(
        user_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Foydalanuvchini tizimdan o'chirish
    """
    # User mavjudligini tekshirish
    existing_user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    existing_user = existing_user_result.fetchone()

    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # User o'chirish
    await session.execute(delete(user).where(user.c.id == user_id))
    await session.commit()

    return SuccessResponse(message=f"Foydalanuvchi {existing_user.email} muvaffaqiyatli o'chirildi")


# --- 5. USER ACTIVE/INACTIVE TOGGLE ---
@router.patch("/users/{user_id}/toggle-active", response_model=UserToggleResponse,
              summary="User active/inactive toggle")
async def toggle_user_active(
        user_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Foydalanuvchining active holatini o'zgartirish
    """
    # User topish
    user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # Active holatini o'zgartirish
    new_active_status = not user_data.is_active
    await session.execute(
        update(user).where(user.c.id == user_id).values(is_active=new_active_status)
    )
    await session.commit()

    # Yangi statistikalarni hisoblash
    active_count_result = await session.execute(
        select(func.count(user.c.id)).where(user.c.is_active == True)
    )
    inactive_count_result = await session.execute(
        select(func.count(user.c.id)).where(user.c.is_active == False)
    )

    active_user_count = active_count_result.scalar()
    inactive_user_count = inactive_count_result.scalar()

    return UserToggleResponse(
        is_active=new_active_status,
        active_user_count=active_user_count,
        inactive_user_count=inactive_user_count
    )


# --- 6. BARCHA USERLARGA XABAR YUBORISH ---
@router.post("/send-message-all", response_model=SuccessResponse, summary="Barcha userlarga xabar yuborish")
async def send_message_to_all(
        message_data: MessageToAllRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Tizimda mavjud barcha foydalanuvchilarga xabar yuborish
    """
    # Barcha userlarni olish
    users_result = await session.execute(select(user))
    all_users = users_result.fetchall()

    # Har bir userga xabar yuborish
    messages_to_insert = []
    for user_data in all_users:
        message_dict = {
            "sender_id": current_user.id,
            "receiver_id": user_data.id,
            "subject": message_data.subject,
            "body": message_data.body,
            "sent_at": datetime.now()
        }
        messages_to_insert.append(message_dict)

    # Barcha xabarlarni bazaga qo'shish
    if messages_to_insert:
        await session.execute(insert(message).values(messages_to_insert))
        await session.commit()

    return SuccessResponse(message=f"Xabar {len(all_users)} ta foydalanuvchiga yuborildi")


# --- 7. BITTA USERGA XABAR YUBORISH ---
@router.post("/send-message", response_model=SuccessResponse, summary="Bitta userga xabar yuborish")
async def send_message_to_user(
        message_data: MessageToUserRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Tanlangan foydalanuvchiga xabar yuborish
    """
    # Receiver mavjudligini tekshirish
    receiver_result = await session.execute(
        select(user).where(user.c.id == message_data.receiver_id)
    )
    receiver = receiver_result.fetchone()

    if not receiver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Qabul qiluvchi topilmadi"
        )

    # Xabar yaratish
    message_dict = {
        "sender_id": current_user.id,
        "receiver_id": message_data.receiver_id,
        "subject": message_data.subject,
        "body": message_data.body,
        "sent_at": datetime.now()
    }

    await session.execute(insert(message).values(**message_dict))
    await session.commit()

    return SuccessResponse(message=f"Xabar {receiver.email} ga yuborildi")


# --- 8. CEO YUBORGAN XABARLAR RO'YXATI ---
@router.get("/messages", response_model=MessageListResponse, summary="CEO yuborgan xabarlar")
async def get_ceo_messages(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    CEO tomonidan yuborilgan barcha xabarlar ro'yxati
    """
    # CEO yuborgan xabarlarni olish
    messages_result = await session.execute(
        select(message, user.c.name, user.c.surname, user.c.email)
        .join(user, message.c.receiver_id == user.c.id)
        .where(message.c.sender_id == current_user.id)
        .order_by(message.c.sent_at.desc())
    )
    messages_data = messages_result.fetchall()

    messages_list = []
    for msg in messages_data:
        message_dict = {
            "id": msg.id,
            "receiver_name": f"{msg.name} {msg.surname}",
            "receiver_email": msg.email,
            "subject": msg.subject,
            "body": msg.body,
            "sent_at": msg.sent_at.isoformat()
        }
        messages_list.append(message_dict)

    return MessageListResponse(messages=messages_list)


# --- 9. XABAR O'CHIRISH ---
@router.delete("/messages/{message_id}", response_model=SuccessResponse, summary="Xabar o'chirish")
async def delete_message(
        message_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    CEO tomonidan yuborilgan xabarni o'chirish
    """
    # Xabar mavjudligini va CEO ga tegishliligini tekshirish
    message_result = await session.execute(
        select(message).where(
            message.c.id == message_id,
            message.c.sender_id == current_user.id
        )
    )
    message_data = message_result.fetchone()

    if not message_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Xabar topilmadi yoki sizga tegishli emas"
        )

    # Xabar o'chirish
    await session.execute(delete(message).where(message.c.id == message_id))
    await session.commit()

    return SuccessResponse(message="Xabar muvaffaqiyatli o'chirildi")


# --- 10. TO'LOVLAR BOSHQARUVI ---
@router.get("/payments", response_model=PaymentListResponse, summary="Barcha to'lovlar")
async def get_payments(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Tizimda mavjud barcha to'lovlar ro'yxati
    """
    # Barcha to'lovlarni olish
    payments_result = await session.execute(select(user_payment))
    payments = payments_result.fetchall()

    payments_list = []
    for payment in payments:
        payment_dict = {
            "id": payment.id,
            "project": payment.project,
            "date": payment.date.isoformat(),
            "summ": float(payment.summ),
            "payment": payment.payment
        }
        payments_list.append(payment_dict)

    return PaymentListResponse(payments=payments_list)


@router.post("/payments", response_model=CreateResponse, summary="Yangi to'lov yaratish")
async def create_payment(
        payment_data: PaymentCreateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Yangi to'lov yozuvi yaratish
    """
    # Yangi to'lov yaratish
    payment_dict = {
        "project": payment_data.project,
        "date": payment_data.date,
        "summ": payment_data.summ,
        "payment": payment_data.payment
    }

    result = await session.execute(insert(user_payment).values(**payment_dict))
    await session.commit()

    return CreateResponse(
        message="To'lov muvaffaqiyatli yaratildi",
        id=result.inserted_primary_key[0]
    )


@router.put("/payments/{payment_id}", response_model=SuccessResponse, summary="To'lov ma'lumotlarini yangilash")
async def update_payment(
        payment_id: int,
        payment_data: PaymentUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Mavjud to'lov ma'lumotlarini yangilash
    """
    # To'lov mavjudligini tekshirish
    existing_payment_result = await session.execute(
        select(user_payment).where(user_payment.c.id == payment_id)
    )
    existing_payment = existing_payment_result.fetchone()

    if not existing_payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="To'lov topilmadi"
        )

    # Yangilanadigan ma'lumotlarni tayyorlash
    update_data = payment_data.dict(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi"
        )

    # Ma'lumotlarni yangilash
    await session.execute(
        update(user_payment).where(user_payment.c.id == payment_id).values(**update_data)
    )
    await session.commit()

    return SuccessResponse(message="To'lov muvaffaqiyatli yangilandi")


@router.delete("/payments/{payment_id}", response_model=SuccessResponse, summary="To'lov o'chirish")
async def delete_payment(
        payment_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    To'lov yozuvini tizimdan o'chirish
    """
    # To'lov mavjudligini tekshirish
    existing_payment_result = await session.execute(
        select(user_payment).where(user_payment.c.id == payment_id)
    )
    existing_payment = existing_payment_result.fetchone()

    if not existing_payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="To'lov topilmadi"
        )

    # To'lov o'chirish
    await session.execute(delete(user_payment).where(user_payment.c.id == payment_id))
    await session.commit()

    return SuccessResponse(message="To'lov muvaffaqiyatli o'chirildi")


@router.patch("/payments/{payment_id}/toggle", response_model=PaymentToggleResponse,
              summary="To'lov holatini o'zgartirish")
async def toggle_payment_status(
        payment_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    To'lov holatini (to'langan/to'lanmagan) o'zgartirish
    """
    # To'lov topish
    payment_result = await session.execute(
        select(user_payment).where(user_payment.c.id == payment_id)
    )
    payment_data = payment_result.fetchone()

    if not payment_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="To'lov topilmadi"
        )

    # Payment holatini o'zgartirish
    new_payment_status = not payment_data.payment
    await session.execute(
        update(user_payment).where(user_payment.c.id == payment_id).values(payment=new_payment_status)
    )
    await session.commit()

    return PaymentToggleResponse(
        message="To'lov holati o'zgartirildi",
        payment_id=payment_id,
        payment_status=new_payment_status
    )





































# user.py - Updated endpoints

from fastapi import HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, delete
from schemes.schemes_users import (
    UserPermissionUpdateRequest,
    UserPermissionAddRequest,
    UserPermissionResponse,
    AllUsersPermissionsResponse,
    SuccessResponse
)
from models.user_models import user, user_page_permission, PageName
from database import get_async_session

# --- 11. USER PERMISSIONS OLISH (TRUE/FALSE FORMAT) ---
@router.get("/users/{user_id}/permissions", response_model=UserPermissionResponse, summary="User ruxsatlarini olish")
async def get_user_permissions(
        user_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Foydalanuvchining joriy sahifa ruxsatlarini checkbox format bilan ko'rish
    Response: {"ceo": true, "payment_list": false, "project_toggle": true, ...}
    """
    # User mavjudligini tekshirish
    user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # User permissions olish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == user_id)
    )
    user_permissions = [perm.page_name.value for perm in permissions_result.fetchall()]

    # Barcha sahifalar uchun true/false obyekt yaratish
    permissions_object = {}
    for page in PageName:
        permissions_object[page.value] = page.value in user_permissions

    # Display nomlar bilan ham ko'rsatish
    permissions_display = {}
    for page_name, has_permission in permissions_object.items():
        if page_name == 'ceo':
            permissions_display['Dashboard'] = has_permission
        elif page_name == 'payment_list':
            permissions_display['Payment'] = has_permission
        elif page_name == 'project_toggle':
            permissions_display['Wordpress'] = has_permission
        elif page_name == 'crm':
            permissions_display['Sales CRM'] = has_permission
        elif page_name == 'finance_list':
            permissions_display['Finance'] = has_permission
        elif page_name == 'update_list':
            permissions_display['Update'] = has_permission
        else:
            permissions_display[page_name] = has_permission

    return UserPermissionResponse(
        user_id=user_id,
        user_email=user_data.email,
        user_name=f"{user_data.name} {user_data.surname}",
        permissions=permissions_object,
        # permissions_display=permissions_display,
        active_permissions_count=len(user_permissions),
        total_available_pages=len(PageName)
    )


# --- 12. USER PERMISSIONS YANGILASH (TRUE/FALSE FORMAT) ---
@router.put("/users/{user_id}/permissions", response_model=SuccessResponse, summary="User ruxsatlarini yangilash")
async def update_user_permissions(
        user_id: int,
        permissions_data: UserPermissionUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Foydalanuvchi sahifa ruxsatlarini checkbox format bilan yangilash
    Request format: {"ceo": true, "payment_list": false, "project_toggle": true}
    """
    # User mavjudligini tekshirish
    user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # Schema ni dict formatiga o'tkazish
    permissions_dict = permissions_data.to_dict()

    # Avvalgi barcha ruxsatlarni o'chirish
    await session.execute(
        delete(user_page_permission).where(user_page_permission.c.user_id == user_id)
    )

    # Faqat true bo'lgan ruxsatlarni qo'shish
    permissions_to_insert = []
    enabled_pages = []

    for page_name, is_enabled in permissions_dict.items():
        if is_enabled:  # Faqat true bo'lganlarni qo'shamiz
            permissions_to_insert.append({
                "user_id": user_id,
                "page_name": PageName(page_name)
            })
            enabled_pages.append(page_name)

    # Ruxsatlarni bazaga qo'shish
    if permissions_to_insert:
        await session.execute(insert(user_page_permission).values(permissions_to_insert))

    await session.commit()

    return SuccessResponse(
        message=f"User {user_data.email} ning ruxsatlari yangilandi. Faol sahifalar: {enabled_pages if enabled_pages else 'Hech qanday ruxsat berilmadi'}"
    )


# --- 13. USER GA RUXSAT QO'SHISH (BARCHA SAHIFALAR TRUE/FALSE) ---
@router.post("/users/{user_id}/permissions/add", response_model=SuccessResponse, summary="User ga ruxsat qo'shish")
async def add_user_permission(
        user_id: int,
        permissions_data: UserPermissionUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Foydalanuvchiga sahifa ruxsatlarini qo'shish/yangilash
    True bo'lgan sahifalar permissions ga qo'shiladi, false bo'lganlar qo'shilmaydi
    """
    # User mavjudligini tekshirish
    user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # Schema ni dict formatiga o'tkazish
    permissions_dict = permissions_data.to_dict()

    # Hozirgi permissions olish
    current_permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == user_id)
    )
    current_permissions = [perm.page_name.value for perm in current_permissions_result.fetchall()]

    # Yangi permissions qo'shish
    added_permissions = []
    for page_name, is_enabled in permissions_dict.items():
        if is_enabled and page_name not in current_permissions:
            # Yangi permission qo'shish
            await session.execute(
                insert(user_page_permission).values(
                    user_id=user_id,
                    page_name=PageName(page_name)
                )
            )
            added_permissions.append(page_name)

    await session.commit()

    if added_permissions:
        return SuccessResponse(
            message=f"User {user_data.email} ga quyidagi sahifalar ruxsati qo'shildi: {', '.join(added_permissions)}"
        )
    else:
        return SuccessResponse(
            message=f"User {user_data.email} uchun yangi ruxsatlar qo'shilmadi (barcha kerakli ruxsatlar allaqachon mavjud)"
        )


# --- 13.1. USER GA BITTA RUXSAT QO'SHISH (AGAR KERAK BO'LSA) ---
@router.post("/users/{user_id}/permissions/add-single", response_model=SuccessResponse, summary="User ga ruxsat qo'shish")
async def add_single_user_permission(
        user_id: int,
        permissions_data: UserPermissionAddRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Foydalanuvchiga sahifa ruxsatlarini qo'shish
    True bo'lgan sahifalar permissions ga qo'shiladi, false bo'lganlar qo'shilmaydi
    """
    # User mavjudligini tekshirish
    user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # Schema ni dict formatiga o'tkazish
    permissions_dict = permissions_data.to_dict()

    # Hozirgi permissions olish
    current_permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == user_id)
    )
    current_permissions = [perm.page_name.value for perm in current_permissions_result.fetchall()]

    # Yangi permissions qo'shish
    added_permissions = []
    for page_name, is_enabled in permissions_dict.items():
        if is_enabled and page_name not in current_permissions:
            # Yangi permission qo'shish
            await session.execute(
                insert(user_page_permission).values(
                    user_id=user_id,
                    page_name=PageName(page_name)
                )
            )
            added_permissions.append(page_name)

    await session.commit()

    if added_permissions:
        return SuccessResponse(
            message=f"User {user_data.email} ga quyidagi sahifalar ruxsati qo'shildi: {', '.join(added_permissions)}"
        )
    else:
        return SuccessResponse(
            message=f"User {user_data.email} uchun yangi ruxsatlar qo'shilmadi (barcha kerakli ruxsatlar allaqachon mavjud yoki hech qanday ruxsat tanlanmadi)"
        )

# --- 14. USER DAN RUXSAT OLIB TASHLASH ---
@router.delete("/users/{user_id}/permissions/{page_name}", response_model=SuccessResponse,
               summary="User dan ruxsat olib tashlash")
async def remove_user_permission(
        user_id: int,
        page_name: str,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Foydalanuvchidan sahifa ruxsatini olib tashlash
    """
    # User mavjudligini tekshirish
    user_result = await session.execute(
        select(user).where(user.c.id == user_id)
    )
    user_data = user_result.fetchone()

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foydalanuvchi topilmadi"
        )

    # Page name tekshirish
    valid_pages = [page.value for page in PageName]
    if page_name not in valid_pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Noto'g'ri sahifa nomi: {page_name}. Mavjud sahifalar: {valid_pages}"
        )

    # Ruxsat mavjudligini tekshirish
    existing_permission = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == user_id,
            user_page_permission.c.page_name == PageName(page_name)
        )
    )

    if not existing_permission.fetchone():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {page_name} sahifasiga ruxsatga ega emas"
        )

    # Ruxsatni olib tashlash
    await session.execute(
        delete(user_page_permission).where(
            user_page_permission.c.user_id == user_id,
            user_page_permission.c.page_name == PageName(page_name)
        )
    )
    await session.commit()

    return SuccessResponse(
        message=f"User {user_data.email} dan {page_name} sahifasi ruxsati olib tashlandi"
    )


# --- 15. BARCHA USERLAR VA ULARNING RUXSATLARI ---
@router.get("/users/permissions/overview", response_model=AllUsersPermissionsResponse, summary="Barcha userlar ruxsatlari ko'rinish")
async def get_all_users_permissions_overview(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_ceo_access)
):
    """
    Barcha foydalanuvchilar va ularning ruxsatlarini ko'rish
    """
    # Barcha userlarni olish
    users_result = await session.execute(select(user))
    users_data = users_result.fetchall()

    users_permissions = []
    for user_data in users_data:
        # Har bir user uchun permissions olish
        permissions_result = await session.execute(
            select(user_page_permission.c.page_name)
            .where(user_page_permission.c.user_id == user_data.id)
        )
        permissions = [perm.page_name.value for perm in permissions_result.fetchall()]

        # Permission nomlarini o'zgartirish
        modified_permissions = []
        for perm in permissions:
            if perm == 'ceo':
                modified_permissions.append('Dashboard')
            elif perm == 'payment_list':
                modified_permissions.append('Payment')
            elif perm == 'project_toggle':
                modified_permissions.append('Wordpress')
            elif perm == 'crm':
                modified_permissions.append('Sales CRM')
            elif perm == 'finance_list':
                modified_permissions.append('Finance')
            elif perm == 'update_list':
                modified_permissions.append('Update')
            else:
                modified_permissions.append(perm)

        user_permission_data = {
            "user_id": user_data.id,
            "email": user_data.email,
            "name": f"{user_data.name} {user_data.surname}",
            "role": user_data.role.value,
            "is_active": user_data.is_active,
            "permissions": permissions,
            "permissions_display": modified_permissions,
            "permissions_count": len(permissions)
        }
        users_permissions.append(user_permission_data)

    # Barcha mavjud sahifalar
    all_pages = [page.value for page in PageName]

    return AllUsersPermissionsResponse(
        users=users_permissions,
        total_users=len(users_permissions),
        available_pages=all_pages,
        summary={
            "users_with_ceo_access": len([u for u in users_permissions if "ceo" in u["permissions"]]),
            "users_with_payment_access": len([u for u in users_permissions if "payment_list" in u["permissions"]]),
            "users_with_wordpress_access": len([u for u in users_permissions if "project_toggle" in u["permissions"]]),
            "users_with_crm_access": len([u for u in users_permissions if "crm" in u["permissions"]]),
            "users_with_finance_access": len([u for u in users_permissions if "finance_list" in u["permissions"]]),
            "users_with_update":len([u for u in users_permissions if "update_list" in u["permissions"]]),
        }
    )