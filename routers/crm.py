from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, insert, update, delete, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import List, Optional
from sqlalchemy.sql.expression import cast
from sqlalchemy.sql.sqltypes import String

# Import qilinadigan modellar
from models.admin_models import customer, CustomerStatus
from models.user_models import user_page_permission, PageName
from schemes.crm_schemes import (
    CustomerCreateRequest, CustomerUpdateRequest, CustomerResponse,
    CustomerListResponse, CustomerStatsResponse, SuccessResponse,
    CreateResponse, CustomerDeleteRequest
)
from auth_utils.auth_func import get_current_active_user
from database import get_async_session

router = APIRouter(prefix="/crm", tags=['Sales CRM'])



# --- DECORATOR: CRM huquqini tekshirish ---
def require_crm_access(current_user=Depends(get_current_active_user)):
    """CRM sahifasiga kirish huquqini tekshirish"""
    # Foydalanuvchi huquqlarini tekshirish
    # Bu yerda asenkron funksiya ichida boshqa asenkron funksiyani chaqira olmaymiz
    # Shuning uchun permissions ni har bir endpoint da alohida tekshiramiz
    return current_user


# --- 1. CRM DASHBOARD - Barcha mijozlar ro'yxati ---
@router.get("/dashboard", response_model=CustomerListResponse, summary="Sales CRM Dashboard")
async def crm_dashboard(
        search: Optional[str] = Query(None, description="Qidiruv so'zi"),
        status_filter: Optional[CustomerStatus] = Query(None, description="Status bo'yicha filter"),
        show_all: bool = Query(False, description="Barcha mijozlarni ko'rsatish"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Sales CRM dashboard - barcha mijozlar ro'yxati va statistikalarni ko'rsatadi
    """
    # Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CRM sahifasiga kirish huquqingiz yo'q"
        )

    # Qidiruv va filter logikasi
    query_conditions = []

    if search and search.strip():
        search_term = f"%{search.strip()}%"
        query_conditions.append(
            or_(
                customer.c.full_name.ilike(search_term),
                customer.c.platform.ilike(search_term),
                customer.c.phone_number == search.strip(),
                customer.c.username.ilike(search_term),
                customer.c.assistant_name.ilike(search_term),
                cast(customer.c.status, String).ilike(search_term)  # Cast status to String
            )
        )

    if status_filter and not show_all:
        query_conditions.append(customer.c.status == status_filter)

    # Mijozlarni olish
    if query_conditions:
        customers_result = await session.execute(
            select(customer)
            .where(*query_conditions)
            .order_by(desc(customer.c.created_at))
        )
    else:
        customers_result = await session.execute(
            select(customer).order_by(desc(customer.c.created_at))
        )

    customers_data = customers_result.fetchall()

    # Mijozlar ro'yxatini tayyorlash
    customers_list = []
    for customer_data in customers_data:
        customer_dict = {
            "id": customer_data.id,
            "full_name": customer_data.full_name,
            "platform": customer_data.platform,
            "username": customer_data.username,
            "phone_number": customer_data.phone_number,
            "status": customer_data.status.value,
            "assistant_name": customer_data.assistant_name,
            "notes": customer_data.notes,
            "created_at": customer_data.created_at.isoformat()
        }
        customers_list.append(customer_dict)

    # Status statistikalarini hisoblash
    status_stats_result = await session.execute(
        select(
            func.count(customer.c.id).label('total_customers'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.need_to_call).label('need_to_call'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.contacted).label('contacted'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.project_started).label(
                'project_started'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.continuing).label('continuing'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.finished).label('finished'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.rejected).label('rejected')
        )
    )
    stats = status_stats_result.fetchone()

    # Status counts va percentages hisoblash
    status_counts_result = await session.execute(
        select(customer.c.status, func.count(customer.c.status).label('count'))
        .group_by(customer.c.status)
        .order_by(customer.c.status)
    )
    status_counts_data = status_counts_result.fetchall()

    status_dict = {item.status.value: item.count for item in status_counts_data}

    # Foizlarni hisoblash
    total = stats.total_customers
    status_percentages = {}
    if total > 0:
        for status_key, count in status_dict.items():
            status_percentages[status_key] = round((count / total) * 100, 1)

    # Status choices ro'yxati
    status_choices = [{"value": status.value, "label": status.value.replace("_", " ").title()}
                      for status in CustomerStatus]

    # Foydalanuvchi huquqlarini olish
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

    return CustomerListResponse(
        customers=customers_list,
        status_stats={
            "total_customers": stats.total_customers,
            "need_to_call": stats.need_to_call,
            "contacted": stats.contacted,
            "project_started": stats.project_started,
            "continuing": stats.continuing,
            "finished": stats.finished,
            "rejected": stats.rejected
        },
        status_dict=status_dict,
        status_percentages=status_percentages,
        status_choices=status_choices,
        permissions=modified_permissions,
        selected_status=status_filter.value if status_filter else None
    )


# --- 2. MIJOZ YARATISH ---
@router.post("/customers", response_model=CreateResponse, summary="Yangi mijoz yaratish")
async def create_customer(
        customer_data: CustomerCreateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Yangi mijoz yaratish
    """
    # Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mijoz yaratish huquqingiz yo'q"
        )

    # Telefon raqami mavjudligini tekshirish
    existing_customer_result = await session.execute(
        select(customer).where(customer.c.phone_number == customer_data.phone_number)
    )
    if existing_customer_result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bu telefon raqami allaqachon mavjud"
        )

    # Yangi mijoz yaratish
    customer_dict = {
        "full_name": customer_data.full_name,
        "platform": customer_data.platform,
        "username": customer_data.username,
        "phone_number": customer_data.phone_number,
        "status": customer_data.status,
        "assistant_name": customer_data.assistant_name,
        "notes": customer_data.notes,
        "created_at": datetime.now()
    }

    result = await session.execute(insert(customer).values(**customer_dict))
    await session.commit()

    return CreateResponse(
        message="Mijoz muvaffaqiyatli yaratildi",
        id=result.inserted_primary_key[0]
    )


# --- 3. MIJOZ MA'LUMOTLARINI YANGILASH ---
@router.put("/customers/{customer_id}", response_model=SuccessResponse, summary="Mijoz ma'lumotlarini yangilash")
async def update_customer(
        customer_id: int,
        customer_data: CustomerUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Mavjud mijoz ma'lumotlarini yangilash
    """
    # Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mijoz ma'lumotlarini yangilash huquqingiz yo'q"
        )

    # Mijoz mavjudligini tekshirish
    existing_customer_result = await session.execute(
        select(customer).where(customer.c.id == customer_id)
    )
    existing_customer = existing_customer_result.fetchone()

    if not existing_customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mijoz topilmadi"
        )

    # Yangilanadigan ma'lumotlarni tayyorlash
    update_data = customer_data.dict(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi"
        )

    # Telefon raqami tekshiruvi (agar yangilanayotgan bo'lsa)
    if "phone_number" in update_data:
        phone_check_result = await session.execute(
            select(customer).where(
                customer.c.phone_number == update_data["phone_number"],
                customer.c.id != customer_id
            )
        )
        if phone_check_result.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bu telefon raqami boshqa mijozda mavjud"
            )

    # Ma'lumotlarni yangilash
    await session.execute(
        update(customer).where(customer.c.id == customer_id).values(**update_data)
    )
    await session.commit()

    return SuccessResponse(message="Mijoz ma'lumotlari muvaffaqiyatli yangilandi")


# --- 4. MIJOZNI O'CHIRISH ---
@router.delete("/customers/{customer_id}", response_model=SuccessResponse, summary="Mijozni o'chirish")
async def delete_customer(
        customer_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Mijozni tizimdan o'chirish
    """
    # Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mijozni o'chirish huquqingiz yo'q"
        )

    # Mijoz mavjudligini tekshirish
    existing_customer_result = await session.execute(
        select(customer).where(customer.c.id == customer_id)
    )
    existing_customer = existing_customer_result.fetchone()

    if not existing_customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mijoz topilmadi"
        )

    # Mijozni o'chirish
    await session.execute(delete(customer).where(customer.c.id == customer_id))
    await session.commit()

    return SuccessResponse(message=f"Mijoz {existing_customer.full_name} muvaffaqiyatli o'chirildi")


# --- 5. BITTA MIJOZ MA'LUMOTLARINI OLISH ---
@router.get("/customers/{customer_id}", response_model=CustomerResponse, summary="Mijoz ma'lumotlarini olish")
async def get_customer(
        customer_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Bitta mijozning batafsil ma'lumotlarini olish
    """
    # Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mijoz ma'lumotlarini ko'rish huquqingiz yo'q"
        )

    # Mijozni topish
    customer_result = await session.execute(
        select(customer).where(customer.c.id == customer_id)
    )
    customer_data = customer_result.fetchone()

    if not customer_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mijoz topilmadi"
        )

    return CustomerResponse(
        id=customer_data.id,
        full_name=customer_data.full_name,
        platform=customer_data.platform,
        username=customer_data.username,
        phone_number=customer_data.phone_number,
        status=customer_data.status.value,
        assistant_name=customer_data.assistant_name,
        notes=customer_data.notes,
        created_at=customer_data.created_at.isoformat()
    )


# --- 6. STATUS STATISTIKALARINI OLISH ---
@router.get("/stats", response_model=CustomerStatsResponse, summary="Mijozlar statistikasi")
async def get_customer_stats(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Mijozlar statistikasini olish
    """
    # Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Statistikani ko'rish huquqingiz yo'q"
        )

    # Status statistikalarini hisoblash
    status_stats_result = await session.execute(
        select(
            func.count(customer.c.id).label('total_customers'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.need_to_call).label('need_to_call'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.contacted).label('contacted'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.project_started).label(
                'project_started'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.continuing).label('continuing'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.finished).label('finished'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.rejected).label('rejected')
        )
    )
    stats = status_stats_result.fetchone()

    # Status counts
    status_counts_result = await session.execute(
        select(customer.c.status, func.count(customer.c.status).label('count'))
        .group_by(customer.c.status)
        .order_by(customer.c.status)
    )
    status_counts_data = status_counts_result.fetchall()

    status_dict = {item.status.value: item.count for item in status_counts_data}

    # Foizlarni hisoblash
    total = stats.total_customers
    status_percentages = {}
    if total > 0:
        for status_key, count in status_dict.items():
            status_percentages[status_key] = round((count / total) * 100, 1)

    return CustomerStatsResponse(
        total_customers=stats.total_customers,
        need_to_call=stats.need_to_call,
        contacted=stats.contacted,
        project_started=stats.project_started,
        continuing=stats.continuing,
        finished=stats.finished,
        rejected=stats.rejected,
        status_dict=status_dict,
        status_percentages=status_percentages
    )


# --- 7. BULK DELETE (Ko'p mijozlarni o'chirish) ---
@router.post("/customers/bulk-delete", response_model=SuccessResponse, summary="Ko'p mijozlarni o'chirish")
async def bulk_delete_customers(
        delete_data: CustomerDeleteRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Bir nechta mijozlarni bir vaqtda o'chirish
    """
    # Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mijozlarni o'chirish huquqingiz yo'q"
        )

    if not delete_data.customer_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O'chiriladigan mijozlar ro'yxati bo'sh"
        )

    # Mijozlarni o'chirish
    await session.execute(
        delete(customer).where(customer.c.id.in_(delete_data.customer_ids))
    )
    await session.commit()

    return SuccessResponse(message=f"{len(delete_data.customer_ids)} ta mijoz muvaffaqiyatli o'chirildi")


# crm.py fayli boshida
from fastapi import Request

# crm_schemes.py fayli boshida
from models.admin_models import CustomerStatus

from schemes.crm_schemes import CustomerAPICreateRequest



@router.post("/api/customers", response_model=CreateResponse, summary="API orqali mijoz yaratish")
async def create_customer_api(
        customer_data: CustomerAPICreateRequest,
        request: Request,
        session: AsyncSession = Depends(get_async_session)
):
    """
    API orqali mijoz yaratish (token autentifikatsiya bilan)
    """
    # Token tekshirish
    token = request.headers.get('X-API-TOKEN')
    if token != "your_secret_api_token":  # Bu yerda settings dan oling
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden"
        )

    # Telefon raqami mavjudligini tekshirish
    existing_customer_result = await session.execute(
        select(customer).where(customer.c.phone_number == customer_data.phone_number)
    )
    if existing_customer_result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bu telefon raqami allaqachon mavjud"
        )

    # Yangi mijoz yaratish
    customer_dict = {
        "full_name": customer_data.full_name,
        "platform": customer_data.platform,
        "username": customer_data.username,
        "phone_number": customer_data.phone_number,
        "status": customer_data.status,
        "assistant_name": customer_data.assistant_name,
        "notes": customer_data.notes,
        "created_at": datetime.now()
    }

    result = await session.execute(insert(customer).values(**customer_dict))
    await session.commit()

    return CreateResponse(
        message="Mijoz muvaffaqiyatli yaratildi",
        id=result.inserted_primary_key[0]
    )