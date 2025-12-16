from fastapi import APIRouter, Depends, HTTPException, status, Query,Form,UploadFile,File
from sqlalchemy import select, insert, update, delete, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import List, Optional
from sqlalchemy.sql.expression import cast
from sqlalchemy.sql.sqltypes import String
from utils.crypto import decrypt_text
from fastapi.responses import RedirectResponse
from telegram.error import TelegramError
# Import qilinadigan modellar
from models.admin_models import customer, CustomerStatus, CustomerType
from models.user_models import user_page_permission, PageName
from schemes.crm_schemes import (
CustomerResponse,
    CustomerListResponse, CustomerStatsResponse, SuccessResponse,
    CreateResponse, CustomerDeleteRequest,ConversationLanguageEnum
)
from datetime import datetime, timedelta
from sqlalchemy import func
from fastapi.responses import StreamingResponse
import requests
from io import BytesIO
from  auth_utils.auth_func import get_current_user
from auth_utils.auth_func import get_current_active_user
from database import get_async_session
from utils.telegram_helper import upload_audio_to_telegram, get_audio_url_from_telegram, validate_audio_file

router = APIRouter(prefix="/crm", tags=['Sales CRM'])



# --- DECORATOR: CRM huquqini tekshirish ---
def require_crm_access(current_user=Depends(get_current_active_user)):

    return current_user






@router.get("/customers/latest", response_model=List[CustomerResponse], summary="Eng so‘nggi mijozlarni olish")
async def get_latest_customers(
    limit: int = Query(50, ge=1, le=500, description="Qaytariladigan mijozlar soni (default: 50)"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_user),
):
    """Eng so‘nggi qo‘shilgan mijozlarni (deshifrlanib) qaytaradi"""
    result = await session.execute(
        select(customer)
        .order_by(desc(customer.c.created_at))
        .limit(limit)
    )

    customers = result.fetchall()
    if not customers:
        raise HTTPException(status_code=404, detail="Mijozlar topilmadi")

    response_list = []
    for c in customers:
        audio_url = None
        if c.audio_file_id:
            # 🔗 Har bir mijoz uchun audio yo‘lini generatsiya qilish
            audio_url = f"https://api.project.cims.cognilabs.org/crm/customers/audio/{c.audio_file_id}"

        response_list.append(CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),        # 🟢 deshifrlanadi
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),  # 🟢 deshifrlanadi
            status=c.status.value,
            assistant_name=c.assistant_name,
            notes=c.notes,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            audio_url=audio_url,                        # 🟢 to‘g‘ri joyda
            created_at=c.created_at.isoformat()
        ))

    return response_list



@router.get("/detail/{customer_id}", response_model=CustomerResponse, summary="Mijoz ma'lumotlarini olish")
async def get_customer_detail(
    customer_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access)
):
    """
    Bitta mijozning batafsil ma'lumotlarini olish.
    Agar mijoz topilmasa yoki boshqa metod chaqirilsa — hech qachon 405 chiqmaydi.
    """
    try:
        result = await session.execute(select(customer).where(customer.c.id == customer_id))
        c = result.fetchone()

        # 🧩 Agar mijoz topilmasa
        if not c:
            raise HTTPException(status_code=404, detail="Mijoz topilmadi")

        # 🎧 Audio URL (agar mavjud bo‘lsa)
        audio_url = None
        if c.audio_file_id:
            audio_url = f"https://api.project.cims.cognilabs.org/crm/customers/audio/{c.audio_file_id}"

        # 🧠 Deshifrlangan ma’lumotlar
        return CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),
            status=c.status.value if hasattr(c.status, "value") else c.status,
            assistant_name=c.assistant_name,
            notes=c.notes,
            audio_file_id=c.audio_file_id,
            audio_url=audio_url,
            conversation_language=c.conversation_language,
            created_at=c.created_at.isoformat()
        )

    except HTTPException as e:
        # Bu "mijoz topilmadi" yoki ruxsat yo‘q holatlari uchun
        raise e
    except Exception as e:
        # ❗ Har qanday kutilmagan xatoliklar uchun
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server xatosi: {str(e)}"
        )

@router.get("/customers/bazakorinish", response_model=List[CustomerResponse], summary="Eng so‘nggi mijozlarni olish")
async def get_latest_customers(
    limit: int = Query(50, ge=1, le=500, description="Qaytariladigan mijozlar soni (default: 50)"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_user),
):
    """Eng so‘nggi qo‘shilgan mijozlarni (deshifrlanib) qaytaradi"""
    result = await session.execute(
        select(customer)
        .order_by(desc(customer.c.created_at))
        .limit(limit)
    )
    customers = result.fetchall()
    if not customers:
        raise HTTPException(status_code=404, detail="Mijozlar topilmadi")

    return [
        CustomerResponse(
            id=c.id,
            full_name=c.full_name,        # 🟢 deshifrlanadi
            platform=c.platform,
            username=c.username,
            phone_number=c.phone_number,  # 🟢 deshifrlanadi
            status=c.status.value,
            assistant_name=c.assistant_name,
            notes=c.notes,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            created_at=c.created_at.isoformat()
        )
        for c in customers
    ]

# 170-341

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
    # Huquq tekshiruvi
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

    # 🔹 Database-dan barcha customerlarni olish (encrypt holda)
    base_query = select(customer).order_by(desc(customer.c.created_at))
    
    # Agar faqat status filter bo'lsa, uni database-da qo'llaymiz (optimizatsiya)
    if status_filter and not show_all and not (search and search.strip()):
        base_query = base_query.where(customer.c.status == status_filter)
    
    customers_result = await session.execute(base_query)
    customers_data = customers_result.fetchall()

    # 🔓 Deshifrlab va filter qilish (Python-da)
    filtered_customers = []
    search_term = search.strip().lower() if search and search.strip() else None

    for c in customers_data:
        # Ma'lumotlarni deshifrlash
        decrypted_name = decrypt_text(c.full_name)
        decrypted_phone = decrypt_text(c.phone_number)
        
        # 🔍 Qidiruv logikasi (deshifrlangan ma'lumotlar bo'yicha)
        if search_term:
            # Barcha maydonlarni tekshirish
            if not any([
                search_term in decrypted_name.lower(),
                search_term in decrypted_phone,
                search_term in (c.platform or "").lower(),
                search_term in (c.username or "").lower(),
                search_term in (c.assistant_name or "").lower(),
                search_term in c.status.value.lower()
            ]):
                continue  # Bu customer mos kelmasa, keyingisiga o'tamiz
        
        # Status filter (agar qidiruv bilan birga bo'lsa)
        if status_filter and not show_all and search_term:
            if c.status != status_filter:
                continue
        
        # Audio URL yaratish
        audio_url = None
        if c.audio_file_id:
            audio_url = f"https://api.project.cims.cognilabs.org/crm/customers/audio/{c.audio_file_id}"
        
        # Ro'yxatga qo'shish
        filtered_customers.append({
            "id": c.id,
            "full_name": decrypted_name,
            "platform": c.platform,
            "username": c.username,
            "phone_number": decrypted_phone,
            "status": c.status.value,
            "assistant_name": c.assistant_name,
            "notes": c.notes,
            "audio_file_id": c.audio_file_id,
            "audio_url": audio_url,
            "conversation_language": c.conversation_language,
            "created_at": c.created_at.isoformat()
        })

    # 🔹 Statistikalarni hisoblash (o'zgarmaydi)
    status_stats_result = await session.execute(
        select(
            func.count(customer.c.id).label('total_customers'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.need_to_call).label('need_to_call'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.contacted).label('contacted'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.project_started).label('project_started'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.continuing).label('continuing'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.finished).label('finished'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.rejected).label('rejected')
        )
    )
    stats = status_stats_result.fetchone()

    status_counts_result = await session.execute(
        select(customer.c.status, func.count(customer.c.status).label('count'))
        .group_by(customer.c.status)
        .order_by(customer.c.status)
    )
    status_counts_data = status_counts_result.fetchall()
    status_dict = {item.status.value: item.count for item in status_counts_data}

    total = stats.total_customers
    status_percentages = {}
    if total > 0:
        for status_key, count in status_dict.items():
            status_percentages[status_key] = round((count / total) * 100, 1)

    status_choices = [{"value": s.value, "label": s.value.replace("_", " ").title()} for s in CustomerStatus]

    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == current_user.id)
    )
    permissions = [perm.page_name.value for perm in permissions_result.fetchall()]
    page_order = ['ceo', 'payment_list', 'project_toggle', 'crm', 'finance_list']
    modified_permissions = []
    for page in page_order:
        if page in permissions:
            mapping = {
                'ceo': 'Dashboard',
                'payment_list': 'Payment',
                'project_toggle': 'Wordpress',
                'crm': 'Sales CRM',
                'finance_list': 'Finance'
            }
            modified_permissions.append(mapping.get(page, page))

    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    week_start = now - timedelta(days=now.weekday())
    month_start = datetime(now.year, now.month, 1)
    three_months_ago = now - timedelta(days=90)
    six_months_ago = now - timedelta(days=180)
    one_year_ago = now - timedelta(days=365)

    period_stats_result = await session.execute(
        select(
            func.count(customer.c.id).filter(customer.c.created_at >= today_start).label("today"),
            func.count(customer.c.id).filter(customer.c.created_at >= week_start).label("this_week"),
            func.count(customer.c.id).filter(customer.c.created_at >= month_start).label("this_month"),
            func.count(customer.c.id).filter(customer.c.created_at >= three_months_ago).label("last_3_months"),
            func.count(customer.c.id).filter(customer.c.created_at >= six_months_ago).label("last_6_months"),
            func.count(customer.c.id).filter(customer.c.created_at >= one_year_ago).label("last_year"),
        )
    )
    period_stats = period_stats_result.fetchone()

    return CustomerListResponse(
        customers=filtered_customers,  # 🟢 Deshifrlangan va filterlangan ro'yxat
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
        selected_status=status_filter.value if status_filter else None,
        period_stats={
            "today": period_stats.today,
            "this_week": period_stats.this_week,
            "this_month": period_stats.this_month,
            "last_3_months": period_stats.last_3_months,
            "last_6_months": period_stats.last_6_months,
            "last_year": period_stats.last_year,
        }
    )


@router.get("/stats/period", summary="CRM davr bo‘yicha mijozlar statistikasi")
async def get_periodic_customer_stats(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):


    # 🧩 Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(func.count()).select_from(customer)
    )
    if not permissions_result:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CRM sahifasiga kirish huquqingiz yo‘q"
        )

    # 🕒 Sana oraliqlarini aniqlash
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    week_start = now - timedelta(days=now.weekday())
    month_start = datetime(now.year, now.month, 1)
    three_months_ago = now - timedelta(days=90)
    six_months_ago = now - timedelta(days=180)
    one_year_ago = now - timedelta(days=365)

    # 🧮 Hisoblash
    query = select(
        func.count(customer.c.id).filter(customer.c.created_at >= today_start).label("today"),
        func.count(customer.c.id).filter(customer.c.created_at >= week_start).label("this_week"),
        func.count(customer.c.id).filter(customer.c.created_at >= month_start).label("this_month"),
        func.count(customer.c.id).filter(customer.c.created_at >= three_months_ago).label("last_3_months"),
        func.count(customer.c.id).filter(customer.c.created_at >= six_months_ago).label("last_6_months"),
        func.count(customer.c.id).filter(customer.c.created_at >= one_year_ago).label("last_year")
    )

    result = await session.execute(query)
    stats = result.fetchone()

    # 🔙 Javob
    return {
        "period_stats": {
            "today": stats.today,
            "this_week": stats.this_week,
            "this_month": stats.this_month,
            "last_3_months": stats.last_3_months,
            "last_6_months": stats.last_6_months,
            "last_year": stats.last_year
        },
        "generated_at": now.isoformat()
    }
from models.admin_models import CustomerStatus


from utils.crypto import encrypt_text


@router.post("/customers", response_model=CreateResponse, summary="Yangi mijoz yaratish")
async def create_customer(
        full_name: str = Form(...),
        platform: str = Form(...),
        phone_number: str = Form(...),
        status: str = Form(...),  # Changed: now accepts string (dynamic status name)
        username: Optional[str] = Form(None),
        assistant_name: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        customer_type: Optional[str] = Form(None),  # NEW: Customer type (default/international)
        conversation_language: Optional[ConversationLanguageEnum] = Form(ConversationLanguageEnum.UZ),
        audio: Optional[UploadFile] = File(None),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Yangi mijoz yaratish - barcha audio formatlar bilan (MP3, OGG, WAV, M4A, ...)
    Status: dinamik status name (string) - masalan: "contacted", "project_started", va hokazo
    Type: "default" (local) yoki "international" - default null
    """
    # Huquq tekshiruvi
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="Mijoz yaratish huquqingiz yo'q")

    # Telefon raqami tekshiruvi
    if not phone_number.strip():
        raise HTTPException(status_code=400, detail="Telefon raqami bo'sh bo'lmasligi kerak")

    # Audio yuklash
    audio_file_id = None
    if audio:
        # Audio faylni validatsiya qilish
        if not validate_audio_file(audio):
            raise HTTPException(
                status_code=400,
                detail=f"Faqat audio fayllar qabul qilinadi. Sizning fayl turi: {audio.content_type}"
            )

        # Telegram ga yuklash
        audio_file_id = await upload_audio_to_telegram(audio)

    # Validate status exists in customer_status table
    from models.admin_models import customer_status_table
    status_result = await session.execute(
        select(customer_status_table)
        .where(
            (customer_status_table.c.name == status) &
            (customer_status_table.c.is_active == True)
        )
    )
    status_obj = status_result.fetchone()

    if not status_obj:
        # Fallback to enum if dynamic status not found
        try:
            enum_status = CustomerStatus[status]
            status_enum_value = enum_status.value
            status_name = status
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"Status '{status}' topilmadi. Mavjud statuslarni /crm/statuses/dynamic endpointidan oling"
            )
    else:
        status_enum_value = status
        status_name = status

    # Shifrlanadigan maydonlar
    encrypted_full_name = encrypt_text(full_name)
    encrypted_phone = encrypt_text(phone_number)

    # Parse customer type
    parsed_type = None
    if customer_type:
        if customer_type.lower() == "international":
            parsed_type = CustomerType.international
        elif customer_type.lower() == "default":
            parsed_type = CustomerType.default

    # Mijozni yaratish
    customer_dict = {
        "full_name": encrypted_full_name,
        "platform": platform,
        "username": username,
        "phone_number": encrypted_phone,
        "status": CustomerStatus.contacted,  # Default enum for backward compatibility
        "status_name": status_name,  # NEW: Dynamic status name
        "type": parsed_type,  # NEW: Customer type (default/international)
        "assistant_name": assistant_name,
        "notes": notes,
        "audio_file_id": audio_file_id,
        "conversation_language": conversation_language.value.upper(),
        "created_at": datetime.now()
    }

    result = await session.execute(insert(customer).values(**customer_dict))
    await session.commit()

    new_customer_id = result.inserted_primary_key[0]

    # Auto-assign sales manager
    from routers.crm_sales_manager import maybe_auto_assign_sales_manager
    try:
        await maybe_auto_assign_sales_manager(new_customer_id, session)
    except Exception:
        pass  # Ignore sales manager assignment errors

    return CreateResponse(
        message="Mijoz muvaffaqiyatli yaratildi",
        id=result.inserted_primary_key[0]
    )


@router.put("/customers/{customer_id}", response_model=SuccessResponse, summary="Mijoz ma'lumotlarini yangilash")
async def update_customer(
        customer_id: int,
        full_name: Optional[str] = Form(None),
        platform: Optional[str] = Form(None),
        phone_number: Optional[str] = Form(None),
        customer_status: Optional[CustomerStatus] = Form(None),
        username: Optional[str] = Form(None),
        assistant_name: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        conversation_language: Optional[ConversationLanguageEnum] = Form(None),
        audio: Optional[UploadFile] = File(None),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Mijoz ma'lumotlarini to'liq yangilash - barcha audio formatlar bilan (MP3, OGG, WAV, M4A, ...)
    """
    # --- 1. Huquqni tekshirish ---
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=403,
            detail="Mijoz ma'lumotlarini yangilash huquqingiz yo'q"
        )

    # --- 2. Mijoz mavjudligini tekshirish ---
    existing_customer_result = await session.execute(
        select(customer).where(customer.c.id == customer_id)
    )
    existing_customer = existing_customer_result.fetchone()

    if not existing_customer:
        raise HTTPException(
            status_code=404,
            detail="Mijoz topilmadi"
        )

    # --- 3. Yangilanadigan ma'lumotlarni tayyorlash ---
    update_data = {}

    if full_name is not None:
        update_data["full_name"] = encrypt_text(full_name)
    if platform is not None:
        update_data["platform"] = platform
    if username is not None:
        update_data["username"] = username
    if phone_number is not None:
        update_data["phone_number"] = encrypt_text(phone_number)
    if customer_status is not None:
        update_data["status"] = customer_status
    if assistant_name is not None:
        update_data["assistant_name"] = assistant_name
    if notes is not None:
        update_data["notes"] = notes
    if conversation_language is not None:
        update_data["conversation_language"] = conversation_language.value.upper()

    # --- 4. Audio yangilash (barcha formatlar) ---
    if audio:
        # Audio faylni validatsiya qilish
        if not validate_audio_file(audio):
            raise HTTPException(
                status_code=400,
                detail=f"Faqat audio fayllar qabul qilinadi. Sizning fayl turi: {audio.content_type}"
            )

        # Telegramga yuklash
        audio_file_id = await upload_audio_to_telegram(audio)
        update_data["audio_file_id"] = audio_file_id

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="Yangilanadigan ma'lumot topilmadi"
        )

    # --- 5. Yangilash va commit ---
    await session.execute(
        update(customer).where(customer.c.id == customer_id).values(**update_data)
    )
    await session.commit()

    return SuccessResponse(message="Mijoz ma'lumotlari muvaffaqiyatli yangilandi")


@router.patch("/customers/{customer_id}", response_model=SuccessResponse, summary="Mijozni qisman yangilash")
async def patch_customer(
        customer_id: int,
        full_name: Optional[str] = Form(None),
        platform: Optional[str] = Form(None),
        phone_number: Optional[str] = Form(None),
        customer_status: Optional[CustomerStatus] = Form(None),
        username: Optional[str] = Form(None),
        assistant_name: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        conversation_language: Optional[ConversationLanguageEnum] = Form(None),
        audio: Optional[UploadFile] = File(None),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Mijozni qisman yangilash - barcha audio formatlar bilan (MP3, OGG, WAV, M4A, ...)
    Faqat yuborilgan maydonlar o'zgaradi
    """
    # Huquqni tekshirish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="Mijozni yangilash huquqingiz yo'q")

    # Mavjud mijozni tekshirish
    result = await session.execute(select(customer).where(customer.c.id == customer_id))
    existing = result.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")

    update_data = {}

    if full_name:
        update_data["full_name"] = encrypt_text(full_name)
    if platform:
        update_data["platform"] = platform
    if phone_number:
        update_data["phone_number"] = encrypt_text(phone_number)
    if customer_status:
        update_data["status"] = customer_status
    if username:
        update_data["username"] = username
    if assistant_name:
        update_data["assistant_name"] = assistant_name
    if notes:
        update_data["notes"] = notes
    if conversation_language:
        update_data["conversation_language"] = conversation_language.value.upper()

    # Audio yangilash (barcha formatlar)
    if audio:
        # Audio faylni validatsiya qilish
        if not validate_audio_file(audio):
            raise HTTPException(
                status_code=400,
                detail=f"Faqat audio fayllar qabul qilinadi. Sizning fayl turi: {audio.content_type}"
            )

        # Telegramga yuklash
        audio_file_id = await upload_audio_to_telegram(audio)
        update_data["audio_file_id"] = audio_file_id

    if not update_data:
        raise HTTPException(status_code=400, detail="Hech qanday maydon yuborilmadi")

    await session.execute(update(customer).where(customer.c.id == customer_id).values(**update_data))
    await session.commit()

    return SuccessResponse(message="Mijoz ma'lumotlari qisman yangilandi")




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


from utils.telegram_helper import  bot

@router.get("/customers/audio/{file_id}", summary="Audio faylni yuklab olish")
async def get_customer_audio(file_id: str):
    """
    Telegramdagi audio faylni yuklab olish va brauzerda o‘ynatish uchun yuborish
    """
    try:
        # Fayl haqida ma'lumot olish
        file = await bot.get_file(file_id)
        file_stream = BytesIO()

        # Faylni yuklab olish (Telegram serveridan to‘g‘ridan-to‘g‘ri oqim bilan)
        await file.download_to_memory(out=file_stream)
        file_stream.seek(0)

        # Oqimni qaytarish
        return StreamingResponse(
            file_stream,
            media_type="audio/mpeg",
            headers={"Content-Disposition": f"inline; filename={file_id}.mp3"}
        )

    except TelegramError as e:
        raise HTTPException(status_code=500, detail=f"Telegram xatolik: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio olishda xatolik: {str(e)}")



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
@router.delete("/customers/bulk-delete", response_model=SuccessResponse, summary="Ko'p mijozlarni o'chirish")
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
    
    customer_dict = {
        "full_name": encrypt_text(customer_data.full_name),
        "platform": customer_data.platform,
        "username": customer_data.username,
        "phone_number": encrypt_text(customer_data.phone_number),
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

from auth_utils.auth_func import  get_current_user



# 1️⃣ STATUS BO‘YICHA FILTER
@router.get("/customers/filter/status", response_model=List[CustomerResponse], summary="Status bo‘yicha mijozlarni filterlash")
async def filter_customers_by_status(
        status_filter: CustomerStatus = Query(..., description="Mijoz statusi bo‘yicha filter"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(get_current_user),  # 🔹 faqat token validatsiya
):
    """Mijozlarni status bo‘yicha filterlaydi"""
    try:
        result = await session.execute(
            select(customer)
            .where(customer.c.status == status_filter)
            .order_by(desc(customer.c.created_at))
        )
        customers = result.fetchall()

        if not customers:
            raise HTTPException(status_code=404, detail="Berilgan status bo‘yicha mijoz topilmadi")

        return [
            CustomerResponse(
                id=c.id,
                full_name=decrypt_text(c.full_name),
                platform=c.platform,
                username=c.username,
                phone_number=decrypt_text(c.phone_number),
                status=c.status.value,
                assistant_name=c.assistant_name,
                notes=c.notes,
                audio_file_id=c.audio_file_id,
                conversation_language=c.conversation_language,
                created_at=c.created_at.isoformat()
            )
            for c in customers
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xatolik: {str(e)}")



# 2️⃣ PLATFORM BO‘YICHA FILTER
@router.get("/customers/filter/platform", response_model=List[CustomerResponse], summary="Platform bo‘yicha mijozlarni filterlash")
async def filter_customers_by_platform(
        platform: str = Query(..., description="Platforma nomi (masalan: Telegram yoki Instagram)"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(get_current_user),
):
    """Mijozlarni platform bo‘yicha filterlaydi"""

    # Platforma nomi bo'yicha filterlash
    result = await session.execute(
        select(customer)
        .where(func.lower(customer.c.platform) == platform.lower())  # Kichik harflarga o'tkazish
        .order_by(desc(customer.c.created_at))
    )
    customers = result.fetchall()

    # Mijozlar bo'lmasa 404 xatolikni yuborish
    if not customers:
        raise HTTPException(status_code=404, detail="Berilgan platforma bo‘yicha mijoz topilmadi")

    return [
        CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),
            status=c.status.value,
            assistant_name=c.assistant_name,
            notes=c.notes,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            created_at=c.created_at.isoformat()
        )
        for c in customers
    ]


from sqlalchemy import and_

# 3️⃣ SANA BO‘YICHA FILTER
@router.get(
    "/customers/filter/date",
    response_model=List[CustomerResponse],
    summary="Sana oralig‘iga ko‘ra mijozlarni filterlash"
)
async def filter_customers_by_date(
    start_date: datetime = Query(..., description="Boshlanish sanasi (YYYY-MM-DD)"),
    end_date: datetime = Query(None, description="Tugash sanasi (YYYY-MM-DD)"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_user),
):
    """
    Sana oralig‘iga yoki bitta sanaga ko‘ra mijozlarni filterlaydi.
    Agar faqat `start_date` berilsa — o‘sha kunlik yozuvlar chiqadi.
    """
  
    # Agar end_date berilmasa, start_date ni o‘sha kun deb olamiz
    if not end_date:
        end_date = start_date  # end_date bo'sh bo'lsa, uni start_date ga tenglashtiramiz

    # Sana oralig'ida filtrlaymiz
    result = await session.execute(
        select(customer)
        .where(and_(
            customer.c.created_at >= start_date,
            customer.c.created_at < end_date + timedelta(days=1)  # 1 kun qo'shish orqali tugash sanasini o'zgartrish
        ))
        .order_by(desc(customer.c.created_at))
    )
    customers = result.fetchall()

    # Mijozlar topilmasa 404 xatolik yuborish
    if not customers:
        raise HTTPException(status_code=404, detail="Berilgan sana oralig‘ida mijoz topilmadi")

    return [
        CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),
            status=c.status.value,
            assistant_name=c.assistant_name,
            notes=c.notes,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            created_at=c.created_at.isoformat()
        )
        for c in customers
    ]

