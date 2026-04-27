from fastapi import APIRouter, Depends, HTTPException, status, Query,Form,UploadFile,File
from sqlalchemy import select, insert, update, delete, func, desc, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, date
from typing import List, Optional
import traceback
from sqlalchemy.sql.expression import cast
from sqlalchemy.sql.sqltypes import String
from utils.crypto import decrypt_text
from fastapi import Request
from fastapi.responses import RedirectResponse
from telegram.error import TelegramError
# Import qilinadigan modellar
from models.admin_models import customer, customer_note, CustomerStatus, CustomerType, customer_status_change_log
from models.user_models import user, user_page_permission, PageName
from schemes.crm_schemes import (
CustomerResponse,
    CustomerListResponse, CustomerStatsResponse, SuccessResponse,
    CreateResponse, CustomerDeleteRequest, ConversationLanguageEnum,
    CustomerNoteCreateRequest, CustomerNoteListResponse, CustomerNoteResponse, CustomerNoteUpdateRequest,
    CustomerPeriodReportResponse, CRMPeriodStatusStats, CRMPeriodicStatusSummaryResponse
)
from datetime import timedelta
from sqlalchemy import func
from fastapi.responses import StreamingResponse
import requests
from io import BytesIO
from zoneinfo import ZoneInfo
from  auth_utils.auth_func import get_current_user
from auth_utils.auth_func import get_current_active_user
from database import get_async_session
from utils.page_permissions import (
    build_permission_display_names,
    get_all_pages,
    get_user_permission_names,
)
from utils.audit import log_audit_event
from utils.telegram_helper import upload_audio_to_telegram, get_audio_url_from_telegram, validate_audio_file
from utils.ai_summary import generate_customer_ai_summary, infer_recall_time_from_notes_ai
from utils.google_calendar import sync_customer_recall_event, delete_customer_recall_event

router = APIRouter(prefix="/crm", tags=['Sales CRM'])

try:
    UZBEKISTAN_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    UZBEKISTAN_TZ = timezone(timedelta(hours=5), name="Asia/Tashkent")


def _debug_customer_create(source: str, message: str) -> None:
    print(f"[customer-create-debug][{source}] {message}", flush=True)


def _safe_decrypt(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return decrypt_text(value)
    except Exception:
        return value


def _to_utc_naive_from_uz(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UZBEKISTAN_TZ)
    else:
        value = value.astimezone(UZBEKISTAN_TZ)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _from_utc_naive_to_uz_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(UZBEKISTAN_TZ).isoformat()


def _date_range_uz_to_utc_naive(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    start_uz = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UZBEKISTAN_TZ)
    end_next_uz = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        tzinfo=UZBEKISTAN_TZ
    ) + timedelta(days=1)
    return (
        start_uz.astimezone(timezone.utc).replace(tzinfo=None),
        end_next_uz.astimezone(timezone.utc).replace(tzinfo=None)
    )


def _build_status_percentages(status_stats: dict[str, int], total: int) -> dict[str, float]:
    percentages: dict[str, float] = {}
    if total > 0:
        for key, count in status_stats.items():
            percentages[key] = round((count / total) * 100, 1)
    else:
        for key in status_stats.keys():
            percentages[key] = 0.0
    return percentages


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_customer_status(value) -> Optional[CustomerStatus]:
    if value is None:
        return None
    if isinstance(value, CustomerStatus):
        return value
    try:
        return CustomerStatus(value)
    except Exception:
        return None


def _calendar_customer_payload(
    customer_id: int,
    *,
    full_name: Optional[str],
    phone_number: Optional[str],
    platform: Optional[str],
    username: Optional[str],
    assistant_name: Optional[str],
    notes: Optional[str],
    recall_time: Optional[datetime],
    status: Optional[str],
) -> dict:
    return {
        "id": customer_id,
        "full_name": full_name,
        "phone_number": phone_number,
        "platform": platform,
        "username": username,
        "assistant_name": assistant_name,
        "notes": notes,
        "recall_time": recall_time,
        "status": status,
    }


async def _sync_customer_calendar_best_effort(customer_data: dict) -> None:
    try:
        await sync_customer_recall_event(customer_data)
    except Exception as exc:
        print(
            f"[google-calendar-sync] customer_id={customer_data.get('id')} sync error: {exc}",
            flush=True,
        )
        print(traceback.format_exc(), flush=True)


async def _delete_customer_calendar_best_effort(customer_id: int) -> None:
    try:
        await delete_customer_recall_event(customer_id)
    except Exception as exc:
        print(f"[google-calendar-sync] customer_id={customer_id} delete error: {exc}", flush=True)
        print(traceback.format_exc(), flush=True)


async def _log_customer_status_change(
    session: AsyncSession,
    customer_id: int,
    from_status,
    to_status
) -> None:
    normalized_from = _normalize_customer_status(from_status)
    normalized_to = _normalize_customer_status(to_status)
    if normalized_to is None:
        return
    if normalized_from == normalized_to:
        return
    try:
        await session.execute(
            insert(customer_status_change_log).values(
                customer_id=customer_id,
                from_status=normalized_from,
                to_status=normalized_to,
                changed_at=_utc_now_naive()
            )
        )
    except Exception:
        # If migration is not applied yet, do not block CRM updates.
        pass


async def _ensure_crm_page_access(session: AsyncSession, current_user, detail: str) -> None:
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm.value,
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail=detail)


async def _ensure_customer_exists(session: AsyncSession, customer_id: int, current_user=None):
    result = await session.execute(select(customer).where(customer.c.id == customer_id))
    existing_customer = result.fetchone()
    if not existing_customer:
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    if current_user is not None and getattr(existing_customer, "is_archived", None):
        if current_user.company_code != "ceo":
            raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    return existing_customer


def _serialize_customer_for_audit(row) -> dict:
    status_value = None
    if getattr(row, "status_name", None):
        status_value = row.status_name
    elif getattr(row, "status", None) is not None:
        status_value = getattr(row.status, "value", row.status)
    customer_type = getattr(row, "type", None)
    return {
        "id": row.id,
        "full_name": _safe_decrypt(row.full_name),
        "platform": row.platform,
        "username": row.username,
        "phone_number": _safe_decrypt(row.phone_number),
        "status": status_value,
        "assistant_name": row.assistant_name,
        "chat_url": getattr(row, "chat_url", None),
        "notes": row.notes,
        "aisummary": row.aisummary,
        "audio_file_id": row.audio_file_id,
        "recall_time": _from_utc_naive_to_uz_iso(row.recall_time),
        "conversation_language": getattr(row, "conversation_language", None),
        "customer_type": getattr(customer_type, "value", customer_type),
        "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "is_archived": getattr(row, "is_archived", None),
    }


def _serialize_customer_note(row) -> CustomerNoteResponse:
    return CustomerNoteResponse(
        id=row.id,
        customer_id=row.customer_id,
        note=row.note,
        created_by=row.created_by,
        created_by_full_name=(
            f"{row.author_name} {row.author_surname}".strip()
            if getattr(row, "author_name", None) or getattr(row, "author_surname", None)
            else None
        ),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _serialize_customer_note_for_audit(row) -> dict:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "note": row.note,
        "created_by": row.created_by,
        "created_by_full_name": (
            f"{row.author_name} {row.author_surname}".strip()
            if getattr(row, "author_name", None) or getattr(row, "author_surname", None)
            else None
        ),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


async def _fetch_customer_note_rows(session: AsyncSession, customer_id: int) -> list:
    author_user = user.alias("author_user")
    result = await session.execute(
        select(
            customer_note.c.id,
            customer_note.c.customer_id,
            customer_note.c.note,
            customer_note.c.created_by,
            customer_note.c.created_at,
            customer_note.c.updated_at,
            author_user.c.name.label("author_name"),
            author_user.c.surname.label("author_surname"),
        )
        .select_from(customer_note.outerjoin(author_user, customer_note.c.created_by == author_user.c.id))
        .where(customer_note.c.customer_id == customer_id)
        .order_by(customer_note.c.created_at.asc(), customer_note.c.id.asc())
    )
    return result.fetchall()


async def _get_status_stats_for_date_range(
    session: AsyncSession,
    start_date: date,
    end_date: date
) -> CRMPeriodStatusStats:
    start_utc_naive, end_utc_naive = _date_range_uz_to_utc_naive(start_date, end_date)

    stats_result = await session.execute(
        select(
            func.count(customer.c.id).label("total_customers"),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.need_to_call).label("need_to_call"),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.contacted).label("contacted"),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.project_started).label("project_started"),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.continuing).label("continuing"),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.finished).label("finished"),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.rejected).label("rejected")
        ).where(
            and_(
                customer.c.created_at >= start_utc_naive,
                customer.c.created_at < end_utc_naive,
                customer.c.is_archived.is_not(True)
            )
        )
    )
    row = stats_result.fetchone()

    status_stats = {
        "need_to_call": row.need_to_call,
        "contacted": row.contacted,
        "project_started": row.project_started,
        "continuing": row.continuing,
        "finished": row.finished,
        "rejected": row.rejected
    }
    total = row.total_customers
    percentages = _build_status_percentages(status_stats, total)

    return CRMPeriodStatusStats(
        total_customers=total,
        status_stats=status_stats,
        status_percentages=percentages
    )



# --- DECORATOR: CRM huquqini tekshirish ---
def require_crm_access(current_user=Depends(get_current_active_user)):

    return current_user






@router.get("/customers/latest", response_model=List[CustomerResponse], summary="Eng soРІР‚Вnggi mijozlarni olish")
async def get_latest_customers(
    limit: int = Query(50, ge=1, le=500, description="Qaytariladigan mijozlar soni (default: 50)"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_user),
):
    """Eng soРІР‚Вnggi qoРІР‚Вshilgan mijozlarni (deshifrlanib) qaytaradi"""
    result = await session.execute(
        select(customer)
        .where(customer.c.is_archived.is_not(True))
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
            # СЂСџвЂќвЂ” Har bir mijoz uchun audio yoРІР‚Вlini generatsiya qilish
            audio_url = f"https://api.project.cims.cognilabs.org/crm/customers/audio/{c.audio_file_id}"

        response_list.append(CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),        # СЂСџСџСћ deshifrlanadi
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),  # СЂСџСџСћ deshifrlanadi
            status=c.status.value,
            assistant_name=c.assistant_name,
            chat_url=c.chat_url,
            notes=c.notes,
            aisummary=c.aisummary,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            audio_url=audio_url,                        # СЂСџСџСћ toРІР‚ВgРІР‚Вri joyda
            recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
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
    Agar mijoz topilmasa yoki boshqa metod chaqirilsa РІР‚вЂќ hech qachon 405 chiqmaydi.
    """
    try:
        c = await _ensure_customer_exists(session, customer_id, current_user)

        # СЂСџР‹В§ Audio URL (agar mavjud boРІР‚Вlsa)
        audio_url = None
        if c.audio_file_id:
            audio_url = f"https://api.project.cims.cognilabs.org/crm/customers/audio/{c.audio_file_id}"
        additional_notes = [_serialize_customer_note(row) for row in await _fetch_customer_note_rows(session, customer_id)]

        # СЂСџВ§В  Deshifrlangan maРІР‚в„ўlumotlar
        return CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),
            status=c.status.value if hasattr(c.status, "value") else c.status,
            assistant_name=c.assistant_name,
            chat_url=c.chat_url,
            notes=c.notes,
            aisummary=c.aisummary,
            audio_file_id=c.audio_file_id,
            audio_url=audio_url,
            conversation_language=c.conversation_language,
            recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
            created_at=c.created_at.isoformat(),
            is_archived=getattr(c, "is_archived", None),
            additional_notes=additional_notes,
        )

    except HTTPException as e:
        # Bu "mijoz topilmadi" yoki ruxsat yoРІР‚Вq holatlari uchun
        raise e
    except Exception as e:
        # РІСњвЂ” Har qanday kutilmagan xatoliklar uchun
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server xatosi: {str(e)}"
        )


@router.get("/customers/{customer_id}/notes", response_model=CustomerNoteListResponse, summary="Mijozning qo'shimcha note'lari")
async def list_customer_notes(
    customer_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    await _ensure_crm_page_access(session, current_user, "Customer note'larni ko'rish huquqingiz yo'q")
    await _ensure_customer_exists(session, customer_id, current_user)
    rows = await _fetch_customer_note_rows(session, customer_id)
    items = [_serialize_customer_note(row) for row in rows]
    return CustomerNoteListResponse(customer_id=customer_id, items=items, total_count=len(items))


@router.post("/customers/{customer_id}/notes", response_model=CustomerNoteResponse, summary="Mijozga qo'shimcha note qo'shish")
async def create_customer_note(
    customer_id: int,
    payload: CustomerNoteCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    await _ensure_crm_page_access(session, current_user, "Customer note qo'shish huquqingiz yo'q")
    await _ensure_customer_exists(session, customer_id, current_user)
    created_at = _utc_now_naive()
    result = await session.execute(
        insert(customer_note)
        .values(
            customer_id=customer_id,
            note=payload.note,
            created_by=current_user.id,
            created_at=created_at,
            updated_at=created_at,
        )
        .returning(customer_note.c.id)
    )
    note_id = result.scalar_one()
    rows = await _fetch_customer_note_rows(session, customer_id)
    created_row = next((row for row in rows if row.id == note_id), None)
    if created_row is None:
        raise HTTPException(status_code=500, detail="Customer note yaratildi, lekin qayta o'qib bo'lmadi")
    after_snapshot = _serialize_customer_note_for_audit(created_row)
    await log_audit_event(
        session,
        module="crm",
        table_name="customer_note",
        entity_type="customer_note",
        entity_id=note_id,
        action="create",
        summary=f"Customer {customer_id} uchun qo'shimcha note yaratildi",
        actor_user=current_user,
        request=request,
        after_data=after_snapshot,
    )
    await session.commit()
    return _serialize_customer_note(created_row)


@router.put("/customers/{customer_id}/notes/{note_id}", response_model=CustomerNoteResponse, summary="Customer note'ni yangilash")
async def update_customer_note(
    customer_id: int,
    note_id: int,
    payload: CustomerNoteUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    await _ensure_crm_page_access(session, current_user, "Customer note'ni yangilash huquqingiz yo'q")
    await _ensure_customer_exists(session, customer_id, current_user)
    existing_result = await session.execute(
        select(customer_note).where(
            customer_note.c.id == note_id,
            customer_note.c.customer_id == customer_id,
        )
    )
    existing_note = existing_result.fetchone()
    if not existing_note:
        raise HTTPException(status_code=404, detail="Customer note topilmadi")
    if current_user.company_code != "ceo" and existing_note.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Siz faqat o'zingiz yozgan notelarni o'zgartira olasiz")
    existing_rows = await _fetch_customer_note_rows(session, customer_id)
    existing_note_row = next((row for row in existing_rows if row.id == note_id), None)
    before_snapshot = _serialize_customer_note_for_audit(existing_note_row) if existing_note_row else None
    await session.execute(
        update(customer_note)
        .where(customer_note.c.id == note_id)
        .values(note=payload.note, updated_at=_utc_now_naive())
    )
    rows = await _fetch_customer_note_rows(session, customer_id)
    updated_row = next((row for row in rows if row.id == note_id), None)
    if updated_row is None:
        raise HTTPException(status_code=500, detail="Customer note yangilandi, lekin qayta o'qib bo'lmadi")
    after_snapshot = _serialize_customer_note_for_audit(updated_row)
    await log_audit_event(
        session,
        module="crm",
        table_name="customer_note",
        entity_type="customer_note",
        entity_id=note_id,
        action="update",
        summary=f"Customer {customer_id} note yangilandi",
        actor_user=current_user,
        request=request,
        before_data=before_snapshot,
        after_data=after_snapshot,
    )
    await session.commit()
    return _serialize_customer_note(updated_row)


@router.delete("/customers/{customer_id}/notes/{note_id}", response_model=SuccessResponse, summary="Customer note'ni o'chirish")
async def delete_customer_note(
    customer_id: int,
    note_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    await _ensure_crm_page_access(session, current_user, "Customer note'ni o'chirish huquqingiz yo'q")
    await _ensure_customer_exists(session, customer_id, current_user)
    existing_rows = await _fetch_customer_note_rows(session, customer_id)
    existing_note_row = next((row for row in existing_rows if row.id == note_id), None)
    if existing_note_row is None:
        raise HTTPException(status_code=404, detail="Customer note topilmadi")
    if current_user.company_code != "ceo" and existing_note_row.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Siz faqat o'zingiz yozgan notelarni o'chira olasiz")
    before_snapshot = _serialize_customer_note_for_audit(existing_note_row)
    result = await session.execute(
        delete(customer_note).where(
            customer_note.c.id == note_id,
            customer_note.c.customer_id == customer_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Customer note topilmadi")
    await log_audit_event(
        session,
        module="crm",
        table_name="customer_note",
        entity_type="customer_note",
        entity_id=note_id,
        action="delete",
        summary=f"Customer {customer_id} note o'chirildi",
        actor_user=current_user,
        request=request,
        before_data=before_snapshot,
    )
    await session.commit()
    return SuccessResponse(message="Customer note muvaffaqiyatli o'chirildi")

@router.get("/customers/bazakorinish", response_model=List[CustomerResponse], summary="Eng soРІР‚Вnggi mijozlarni olish")
async def get_latest_customers(
    limit: int = Query(50, ge=1, le=500, description="Qaytariladigan mijozlar soni (default: 50)"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_user),
):
    """Eng soРІР‚Вnggi qoРІР‚Вshilgan mijozlarni (deshifrlanib) qaytaradi"""
    result = await session.execute(
        select(customer)
        .where(customer.c.is_archived.is_not(True))
        .order_by(desc(customer.c.created_at))
        .limit(limit)
    )
    customers = result.fetchall()
    if not customers:
        raise HTTPException(status_code=404, detail="Mijozlar topilmadi")

    return [
        CustomerResponse(
            id=c.id,
            full_name=c.full_name,        # СЂСџСџСћ deshifrlanadi
            platform=c.platform,
            username=c.username,
            phone_number=c.phone_number,  # СЂСџСџСћ deshifrlanadi
            status=c.status.value,
            assistant_name=c.assistant_name,
            chat_url=c.chat_url,
            notes=c.notes,
            aisummary=c.aisummary,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
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
        page: int = Query(1, ge=1, description="Sahifa raqami"),
        page_size: int = Query(50, ge=1, le=50, description="Sahifadagi mijozlar soni (max 50)"),
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
            user_page_permission.c.page_name == PageName.crm.value
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CRM sahifasiga kirish huquqingiz yo'q"
        )

    # СЂСџвЂќв„– Database-dan barcha customerlarni olish (encrypt holda)
    base_query = select(customer).where(customer.c.is_archived.is_not(True)).order_by(desc(customer.c.created_at))
    
    # Default holatda rejected customerlar chiqmasin.
    # Faqat status_filter orqali rejected tanlanganda ko'rsatiladi.
    if not status_filter:
        base_query = base_query.where(customer.c.status != CustomerStatus.rejected)
    # Agar faqat status filter bo'lsa, uni database-da qo'llaymiz (optimizatsiya)
    elif not show_all and not (search and search.strip()):
        base_query = base_query.where(customer.c.status == status_filter)
    
    customers_result = await session.execute(base_query)
    customers_data = customers_result.fetchall()

    # СЂСџвЂќвЂњ Deshifrlab va filter qilish (Python-da)
    filtered_customers = []
    search_term = search.strip().lower() if search and search.strip() else None

    for c in customers_data:
        # Ma'lumotlarni deshifrlash
        decrypted_name = decrypt_text(c.full_name)
        decrypted_phone = decrypt_text(c.phone_number)
        
        # СЂСџвЂќРЊ Qidiruv logikasi (deshifrlangan ma'lumotlar bo'yicha)
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
        
        # Default holatda rejected customerlarni chiqarib yubormaymiz
        if not status_filter and c.status == CustomerStatus.rejected:
            continue

        # Status filter (agar qidiruv bilan birga bo'lsa)
        if status_filter and not show_all and search_term and c.status != status_filter:
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
            "chat_url": c.chat_url,
            "notes": c.notes,
            "aisummary": c.aisummary,
            "audio_file_id": c.audio_file_id,
            "audio_url": audio_url,
            "conversation_language": c.conversation_language,
            "recall_time": _from_utc_naive_to_uz_iso(c.recall_time),
            "created_at": c.created_at.isoformat(),
            "is_archived": getattr(c, "is_archived", None),
        })

    total_items = len(filtered_customers)
    total_pages = (total_items + page_size - 1) // page_size if total_items > 0 else 0
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_customers = filtered_customers[start_idx:end_idx]

    # СЂСџвЂќв„– Statistikalarni hisoblash (o'zgarmaydi)
    status_stats_result = await session.execute(
        select(
            func.count(customer.c.id).label('total_customers'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.need_to_call).label('need_to_call'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.contacted).label('contacted'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.project_started).label('project_started'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.continuing).label('continuing'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.finished).label('finished'),
            func.count(customer.c.id).filter(customer.c.status == CustomerStatus.rejected).label('rejected')
        ).where(customer.c.is_archived.is_not(True))
    )
    stats = status_stats_result.fetchone()

    status_counts_result = await session.execute(
        select(customer.c.status, func.count(customer.c.status).label('count'))
        .where(customer.c.is_archived.is_not(True))
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

    permissions = await get_user_permission_names(session, current_user.id)
    page_display_map = {
        page.name: page.display_name
        for page in await get_all_pages(session)
    }
    modified_permissions = build_permission_display_names(permissions, page_display_map)

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
        ).where(customer.c.is_archived.is_not(True))
    )
    period_stats = period_stats_result.fetchone()

    return CustomerListResponse(
        customers=paginated_customers,  # Deshifrlangan, filterlangan va sahifalangan ro'yxat
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
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


@router.get("/stats/period", summary="CRM davr boРІР‚Вyicha mijozlar statistikasi")
async def get_periodic_customer_stats(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):


    # СЂСџВ§В© Foydalanuvchi huquqini tekshirish
    permissions_result = await session.execute(
        select(func.count()).select_from(customer)
    )
    if not permissions_result:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CRM sahifasiga kirish huquqingiz yoРІР‚Вq"
        )

    # СЂСџвЂўвЂ™ Sana oraliqlarini aniqlash
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    week_start = now - timedelta(days=now.weekday())
    month_start = datetime(now.year, now.month, 1)
    three_months_ago = now - timedelta(days=90)
    six_months_ago = now - timedelta(days=180)
    one_year_ago = now - timedelta(days=365)

    # СЂСџВ§В® Hisoblash
    query = select(
        func.count(customer.c.id).filter(customer.c.created_at >= today_start).label("today"),
        func.count(customer.c.id).filter(customer.c.created_at >= week_start).label("this_week"),
        func.count(customer.c.id).filter(customer.c.created_at >= month_start).label("this_month"),
        func.count(customer.c.id).filter(customer.c.created_at >= three_months_ago).label("last_3_months"),
        func.count(customer.c.id).filter(customer.c.created_at >= six_months_ago).label("last_6_months"),
        func.count(customer.c.id).filter(customer.c.created_at >= one_year_ago).label("last_year")
    ).where(customer.c.is_archived.is_not(True))

    result = await session.execute(query)
    stats = result.fetchone()

    # СЂСџвЂќв„ў Javob
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
        chat_url: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        recall_time: Optional[datetime] = Form(
            None,
            description="Recall vaqti (Asia/Tashkent), masalan: 2026-03-03T09:53:00+05:00",
            example="2026-03-03T09:53:00+05:00"
        ),
        customer_type: Optional[str] = Form(None),  # NEW: Customer type (local/international)
        conversation_language: Optional[ConversationLanguageEnum] = Form(ConversationLanguageEnum.UZ),
        audio: Optional[UploadFile] = File(None),
        request: Request = None,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_crm_access)
):
    """
    Yangi mijoz yaratish - barcha audio formatlar bilan (MP3, OGG, WAV, M4A, ...)
    Status: dinamik status name (string) - masalan: "contacted", "project_started", va hokazo
    Type: "local" yoki "international" - default null
    """
    _debug_customer_create(
        "form",
        f"request received platform={platform} phone={phone_number} notes='{(notes or '')[:220]}' recall_time_input={recall_time}"
    )

    # Huquq tekshiruvi
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.crm.value
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
        elif customer_type.lower() == "local":
            parsed_type = CustomerType.local

    ai_summary = await generate_customer_ai_summary(notes)
    normalized_chat_url = chat_url.strip() if chat_url and chat_url.strip() else None
    created_at_uz = datetime.now(UZBEKISTAN_TZ)
    created_at = created_at_uz.replace(tzinfo=None)
    resolved_recall_time = recall_time
    if resolved_recall_time is None:
        resolved_recall_time = await infer_recall_time_from_notes_ai(
            notes,
            created_at=created_at_uz
        )
    _debug_customer_create(
        "form",
        f"resolved recall_time={resolved_recall_time} created_at_uz={created_at_uz.isoformat()}"
    )

    # Mijozni yaratish
    customer_dict = {
        "full_name": encrypted_full_name,
        "platform": platform,
        "username": username,
        "phone_number": encrypted_phone,
        "status": CustomerStatus.contacted,  # Default enum for backward compatibility
        "status_name": status_name,  # NEW: Dynamic status name
        "type": parsed_type,  # NEW: Customer type (local/international)
        "assistant_name": assistant_name,
        "chat_url": normalized_chat_url,
        "notes": notes,
        "aisummary": ai_summary,
        "audio_file_id": audio_file_id,
        "recall_time": _to_utc_naive_from_uz(resolved_recall_time),
        "conversation_language": conversation_language.value.upper(),
        "created_at": created_at
    }

    result = await session.execute(insert(customer).values(**customer_dict))
    _debug_customer_create(
        "form",
        f"customer inserted id={result.inserted_primary_key[0]} recall_time_saved={customer_dict['recall_time']}"
    )

    new_customer_id = result.inserted_primary_key[0]
    created_customer = await _ensure_customer_exists(session, new_customer_id)
    after_snapshot = _serialize_customer_for_audit(created_customer)
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer",
        entity_id=new_customer_id,
        action="create",
        summary=f"Lead yaratildi: {after_snapshot['full_name']}",
        actor_user=current_user,
        request=request,
        after_data=after_snapshot,
    )
    await session.commit()

    # Auto-assign sales manager
    from routers.crm_sales_manager import maybe_auto_assign_sales_manager
    try:
        await maybe_auto_assign_sales_manager(new_customer_id, session)
    except Exception:
        pass  # Ignore sales manager assignment errors

    await _sync_customer_calendar_best_effort(
        _calendar_customer_payload(
            new_customer_id,
            full_name=full_name,
            phone_number=phone_number,
            platform=platform,
            username=username,
            assistant_name=assistant_name,
            notes=notes,
            recall_time=customer_dict["recall_time"],
            status=status_name,
        )
    )

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
        chat_url: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        recall_time: Optional[datetime] = Form(
            None,
            description="Recall vaqti (Asia/Tashkent), masalan: 2026-03-03T09:53:00+05:00",
            example="2026-03-03T09:53:00+05:00"
        ),
        clear_recall_time: bool = Form(False),
        conversation_language: Optional[ConversationLanguageEnum] = Form(None),
        audio: Optional[UploadFile] = File(None),
        request: Request = None,
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
            user_page_permission.c.page_name == PageName.crm.value
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
    if getattr(existing_customer, "is_archived", None) and current_user.company_code != "ceo":
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    previous_status = existing_customer.status
    current_full_name = _safe_decrypt(existing_customer.full_name)
    current_phone_number = _safe_decrypt(existing_customer.phone_number)
    before_snapshot = _serialize_customer_for_audit(existing_customer)

    # --- 3. Yangilanadigan ma'lumotlarni tayyorlash ---
    update_data = {}
    normalized_chat_url = chat_url.strip() if chat_url and chat_url.strip() else None

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
        update_data["status_name"] = customer_status.value
    if assistant_name is not None:
        update_data["assistant_name"] = assistant_name
    if chat_url is not None:
        update_data["chat_url"] = normalized_chat_url
    if notes is not None:
        update_data["notes"] = notes
        update_data["aisummary"] = await generate_customer_ai_summary(notes)
    if clear_recall_time:
        update_data["recall_time"] = None
    elif recall_time is not None:
        update_data["recall_time"] = _to_utc_naive_from_uz(recall_time)
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
    updated_customer = await _ensure_customer_exists(session, customer_id)
    after_snapshot = _serialize_customer_for_audit(updated_customer)
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer",
        entity_id=customer_id,
        action="update",
        summary=f"Lead yangilandi: {after_snapshot['full_name']}",
        actor_user=current_user,
        request=request,
        before_data=before_snapshot,
        after_data=after_snapshot,
    )
    if customer_status is not None:
        await _log_customer_status_change(
            session=session,
            customer_id=customer_id,
            from_status=previous_status,
            to_status=customer_status
        )
        await log_audit_event(
            session,
            module="crm",
            table_name="customer",
            entity_type="customer",
            entity_id=customer_id,
            action="status_change",
            summary=f"Lead status o'zgardi: {before_snapshot['status']} -> {after_snapshot['status']}",
            actor_user=current_user,
            request=request,
            before_data={"status": before_snapshot["status"]},
            after_data={"status": after_snapshot["status"]},
        )
    await session.commit()

    calendar_recall_time = None if clear_recall_time else update_data.get("recall_time", existing_customer.recall_time)
    calendar_status = (
        customer_status.value if customer_status is not None
        else existing_customer.status_name or existing_customer.status.value
    )
    await _sync_customer_calendar_best_effort(
        _calendar_customer_payload(
            customer_id,
            full_name=full_name if full_name is not None else current_full_name,
            phone_number=phone_number if phone_number is not None else current_phone_number,
            platform=platform if platform is not None else existing_customer.platform,
            username=username if username is not None else existing_customer.username,
            assistant_name=assistant_name if assistant_name is not None else existing_customer.assistant_name,
            notes=notes if notes is not None else existing_customer.notes,
            recall_time=calendar_recall_time,
            status=calendar_status,
        )
    )

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
        chat_url: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        recall_time: Optional[datetime] = Form(
            None,
            description="Recall vaqti (Asia/Tashkent), masalan: 2026-03-03T09:53:00+05:00",
            example="2026-03-03T09:53:00+05:00"
        ),
        clear_recall_time: bool = Form(False),
        conversation_language: Optional[ConversationLanguageEnum] = Form(None),
        audio: Optional[UploadFile] = File(None),
        request: Request = None,
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
            user_page_permission.c.page_name == PageName.crm.value
        )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="Mijozni yangilash huquqingiz yo'q")

    # Mavjud mijozni tekshirish
    result = await session.execute(select(customer).where(customer.c.id == customer_id))
    existing = result.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    if getattr(existing, "is_archived", None) and current_user.company_code != "ceo":
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    previous_status = existing.status
    current_full_name = _safe_decrypt(existing.full_name)
    current_phone_number = _safe_decrypt(existing.phone_number)
    before_snapshot = _serialize_customer_for_audit(existing)

    update_data = {}
    normalized_chat_url = chat_url.strip() if chat_url and chat_url.strip() else None

    if full_name:
        update_data["full_name"] = encrypt_text(full_name)
    if platform:
        update_data["platform"] = platform
    if phone_number:
        update_data["phone_number"] = encrypt_text(phone_number)
    if customer_status:
        update_data["status"] = customer_status
        update_data["status_name"] = customer_status.value
    if username:
        update_data["username"] = username
    if assistant_name:
        update_data["assistant_name"] = assistant_name
    if chat_url is not None:
        update_data["chat_url"] = normalized_chat_url
    if notes is not None:
        update_data["notes"] = notes
        update_data["aisummary"] = await generate_customer_ai_summary(notes)
    if clear_recall_time:
        update_data["recall_time"] = None
    elif recall_time is not None:
        update_data["recall_time"] = _to_utc_naive_from_uz(recall_time)
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
    updated_customer = await _ensure_customer_exists(session, customer_id)
    after_snapshot = _serialize_customer_for_audit(updated_customer)
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer",
        entity_id=customer_id,
        action="update",
        summary=f"Lead qisman yangilandi: {after_snapshot['full_name']}",
        actor_user=current_user,
        request=request,
        before_data=before_snapshot,
        after_data=after_snapshot,
    )
    if customer_status is not None:
        await _log_customer_status_change(
            session=session,
            customer_id=customer_id,
            from_status=previous_status,
            to_status=customer_status
        )
        await log_audit_event(
            session,
            module="crm",
            table_name="customer",
            entity_type="customer",
            entity_id=customer_id,
            action="status_change",
            summary=f"Lead status o'zgardi: {before_snapshot['status']} -> {after_snapshot['status']}",
            actor_user=current_user,
            request=request,
            before_data={"status": before_snapshot["status"]},
            after_data={"status": after_snapshot["status"]},
        )
    await session.commit()

    calendar_recall_time = None if clear_recall_time else update_data.get("recall_time", existing.recall_time)
    calendar_status = (
        customer_status.value if customer_status is not None
        else existing.status_name or existing.status.value
    )
    await _sync_customer_calendar_best_effort(
        _calendar_customer_payload(
            customer_id,
            full_name=full_name if full_name is not None else current_full_name,
            phone_number=phone_number if phone_number is not None else current_phone_number,
            platform=platform if platform is not None else existing.platform,
            username=username if username is not None else existing.username,
            assistant_name=assistant_name if assistant_name is not None else existing.assistant_name,
            notes=notes if notes is not None else existing.notes,
            recall_time=calendar_recall_time,
            status=calendar_status,
        )
    )

    return SuccessResponse(message="Mijoz ma'lumotlari qisman yangilandi")




# --- 4. MIJOZNI O'CHIRISH ---
@router.delete("/customers/{customer_id}", response_model=SuccessResponse, summary="Mijozni o'chirish")
async def delete_customer(
        customer_id: int,
        request: Request,
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
            user_page_permission.c.page_name == PageName.crm.value
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
    before_snapshot = _serialize_customer_for_audit(existing_customer)

    # Soft delete - archive
    await session.execute(
        update(customer).where(customer.c.id == customer_id).values(is_archived=True)
    )
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer",
        entity_id=customer_id,
        action="archive",
        summary=f"Lead arxivlandi: {before_snapshot['full_name']}",
        actor_user=current_user,
        request=request,
        before_data=before_snapshot,
    )
    await session.commit()
    await _delete_customer_calendar_best_effort(customer_id)

    return SuccessResponse(message=f"Mijoz {before_snapshot['full_name']} muvaffaqiyatli arxivlandi")


from utils.telegram_helper import  bot

@router.get("/customers/audio/{file_id}", summary="Audio faylni yuklab olish")
async def get_customer_audio(file_id: str):
    """
    Telegramdagi audio faylni yuklab olish va brauzerda oРІР‚Вynatish uchun yuborish
    """
    try:
        # Fayl haqida ma'lumot olish
        file = await bot.get_file(file_id)
        file_stream = BytesIO()

        # Faylni yuklab olish (Telegram serveridan toРІР‚ВgРІР‚Вridan-toРІР‚ВgРІР‚Вri oqim bilan)
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
            user_page_permission.c.page_name == PageName.crm.value
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
        request: Request,
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
            user_page_permission.c.page_name == PageName.crm.value
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

    # Soft delete - archive
    customers_result = await session.execute(
        select(customer).where(customer.c.id.in_(delete_data.customer_ids))
    )
    existing_customers = customers_result.fetchall()
    existing_customer_ids = [row.id for row in existing_customers]
    await session.execute(
        update(customer).where(customer.c.id.in_(delete_data.customer_ids)).values(is_archived=True)
    )
    for existing_customer in existing_customers:
        before_snapshot = _serialize_customer_for_audit(existing_customer)
        await log_audit_event(
            session,
            module="crm",
            table_name="customer",
            entity_type="customer",
            entity_id=existing_customer.id,
            action="archive",
            summary=f"Lead bulk arxivlandi: {before_snapshot['full_name']}",
            actor_user=current_user,
            request=request,
            before_data=before_snapshot,
        )
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer_bulk",
        entity_id=None,
        action="bulk_archive",
        summary=f"{len(existing_customer_ids)} ta lead bulk arxivlandi",
        actor_user=current_user,
        request=request,
        before_data={"customer_ids": existing_customer_ids},
    )
    await session.commit()
    for customer_id in existing_customer_ids:
        await _delete_customer_calendar_best_effort(customer_id)

    return SuccessResponse(message=f"{len(delete_data.customer_ids)} ta mijoz muvaffaqiyatli arxivlandi")


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
    _debug_customer_create(
        "api",
        f"request received platform={customer_data.platform} phone={customer_data.phone_number} notes='{(customer_data.notes or '')[:220]}' recall_time_input={customer_data.recall_time}"
    )

    created_at_uz = datetime.now(UZBEKISTAN_TZ)
    created_at = created_at_uz.replace(tzinfo=None)
    resolved_recall_time = customer_data.recall_time
    if resolved_recall_time is None:
        resolved_recall_time = await infer_recall_time_from_notes_ai(
            customer_data.notes,
            created_at=created_at_uz
        )
    _debug_customer_create(
        "api",
        f"resolved recall_time={resolved_recall_time} created_at_uz={created_at_uz.isoformat()}"
    )

    ai_summary = await generate_customer_ai_summary(customer_data.notes)

    customer_dict = {
        "full_name": encrypt_text(customer_data.full_name),
        "platform": customer_data.platform,
        "username": customer_data.username,
        "phone_number": encrypt_text(customer_data.phone_number),
        "status": customer_data.status,
        "assistant_name": customer_data.assistant_name,
        "chat_url": customer_data.chat_url,
        "notes": customer_data.notes,
        "aisummary": ai_summary,
        "recall_time": _to_utc_naive_from_uz(resolved_recall_time),
        "created_at": created_at
    }

    result = await session.execute(insert(customer).values(**customer_dict))
    _debug_customer_create(
        "api",
        f"customer inserted id={result.inserted_primary_key[0]} recall_time_saved={customer_dict['recall_time']}"
    )

    new_customer_id = result.inserted_primary_key[0]
    created_customer = await _ensure_customer_exists(session, new_customer_id)
    after_snapshot = _serialize_customer_for_audit(created_customer)
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer",
        entity_id=new_customer_id,
        action="create",
        summary=f"Lead API orqali yaratildi: {after_snapshot['full_name']}",
        request=request,
        after_data=after_snapshot,
        is_system_action=True,
    )
    await session.commit()
    await _sync_customer_calendar_best_effort(
        _calendar_customer_payload(
            new_customer_id,
            full_name=customer_data.full_name,
            phone_number=customer_data.phone_number,
            platform=customer_data.platform,
            username=customer_data.username,
            assistant_name=customer_data.assistant_name,
            notes=customer_data.notes,
            recall_time=customer_dict["recall_time"],
            status=customer_data.status.value,
        )
    )

    return CreateResponse(
        message="Mijoz muvaffaqiyatli yaratildi",
        id=new_customer_id
    )

from auth_utils.auth_func import  get_current_user



# 1РїС‘РЏРІС“Р€ STATUS BO'YICHA FILTER (Dynamic - customer_status jadvalidan)
@router.get("/customers/filter/status", response_model=List[CustomerResponse], summary="Status bo'yicha mijozlarni filterlash")
async def filter_customers_by_status(
        status_filter: str = Query(..., description="Mijoz statusi bo'yicha filter (dynamic status name)"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(get_current_user),  # СЂСџвЂќв„– faqat token validatsiya
):
    """
    Mijozlarni status bo'yicha filterlaydi (dynamic status)
    status_filter: status name (masalan: 'contacted', 'kerak', 'project_started')
    """
    try:
        # Filter by status_name (dynamic status) instead of status enum
        result = await session.execute(
            select(customer)
            .where(customer.c.status_name == status_filter, customer.c.is_archived.is_not(True))
            .order_by(desc(customer.c.created_at))
        )
        customers = result.fetchall()

        if not customers:
            raise HTTPException(status_code=404, detail=f"'{status_filter}' status bo'yicha mijoz topilmadi")

        return [
            CustomerResponse(
                id=c.id,
                full_name=decrypt_text(c.full_name),
                platform=c.platform,
                username=c.username,
                phone_number=decrypt_text(c.phone_number),
                status=c.status_name or c.status.value,  # Use status_name if available
                assistant_name=c.assistant_name,
                chat_url=c.chat_url,
                notes=c.notes,
                aisummary=c.aisummary,
                audio_file_id=c.audio_file_id,
                conversation_language=c.conversation_language,
                recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
                created_at=c.created_at.isoformat()
            )
            for c in customers
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xatolik: {str(e)}")



# 2РїС‘РЏРІС“Р€ PLATFORM BOРІР‚ВYICHA FILTER
@router.get("/customers/filter/platform", response_model=List[CustomerResponse], summary="Platform boРІР‚Вyicha mijozlarni filterlash")
async def filter_customers_by_platform(
        platform: str = Query(..., description="Platforma nomi (masalan: Telegram yoki Instagram)"),
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(get_current_user),
):
    """Mijozlarni platform boРІР‚Вyicha filterlaydi"""

    # Platforma nomi bo'yicha filterlash
    result = await session.execute(
        select(customer)
        .where(func.lower(customer.c.platform) == platform.lower(), customer.c.is_archived.is_not(True))
        .order_by(desc(customer.c.created_at))
    )
    customers = result.fetchall()

    # Mijozlar bo'lmasa 404 xatolikni yuborish
    if not customers:
        raise HTTPException(status_code=404, detail="Berilgan platforma boРІР‚Вyicha mijoz topilmadi")

    return [
        CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),
            status=c.status.value,
            assistant_name=c.assistant_name,
            chat_url=c.chat_url,
            notes=c.notes,
            aisummary=c.aisummary,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
            created_at=c.created_at.isoformat()
        )
        for c in customers
    ]


@router.get(
    "/customers/stats/summary",
    response_model=CRMPeriodicStatusSummaryResponse,
    summary="Bugun/3 kun/1 hafta/1 oy/3 oy bo'yicha status statistikasi"
)
async def customers_periodic_status_summary(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access)
):
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name).where(
            and_(
                user_page_permission.c.user_id == current_user.id,
                user_page_permission.c.page_name == PageName.crm.value
            )
    )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="CRM sahifasiga kirish huquqingiz yo'q")

    today_uz = datetime.now(UZBEKISTAN_TZ).date()

    today_stats = await _get_status_stats_for_date_range(session, today_uz, today_uz)
    last_3_days_stats = await _get_status_stats_for_date_range(session, today_uz - timedelta(days=2), today_uz)
    last_7_days_stats = await _get_status_stats_for_date_range(session, today_uz - timedelta(days=6), today_uz)
    last_30_days_stats = await _get_status_stats_for_date_range(session, today_uz - timedelta(days=29), today_uz)
    last_90_days_stats = await _get_status_stats_for_date_range(session, today_uz - timedelta(days=89), today_uz)

    return CRMPeriodicStatusSummaryResponse(
        generated_at=datetime.now(UZBEKISTAN_TZ).isoformat(),
        today=today_stats,
        last_3_days=last_3_days_stats,
        last_7_days=last_7_days_stats,
        last_30_days=last_30_days_stats,
        last_90_days=last_90_days_stats
    )


@router.get(
    "/customers/report/period",
    response_model=CustomerPeriodReportResponse,
    summary="Davr bo'yicha customerlar ro'yxati va status statistikasi"
)
async def customers_period_report(
    period: str = Query(
        "7d",
        description="Davr: 3d, 7d, 15d, 30d. Agar from_date/to_date berilsa custom ishlaydi"
    ),
    search: Optional[str] = Query(None, description="Qidiruv so'zi"),
    status_filter: Optional[CustomerStatus] = Query(None, description="Status bo'yicha filter"),
    from_date: Optional[date] = Query(None, description="Boshlanish sana (YYYY-MM-DD)"),
    to_date: Optional[date] = Query(None, description="Tugash sana (YYYY-MM-DD)"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access)
):
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name).where(
            and_(
                user_page_permission.c.user_id == current_user.id,
                user_page_permission.c.page_name == PageName.crm.value
            )
    )
    )
    if not permissions_result.fetchone() and current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="CRM sahifasiga kirish huquqingiz yo'q")

    has_from = from_date is not None
    has_to = to_date is not None

    if has_from != has_to:
        raise HTTPException(
            status_code=400,
            detail="from_date va to_date ikkalasi birga yuborilishi kerak"
        )

    periods = {
        "3d": 3,
        "7d": 7,
        "15d": 15,
        "30d": 30
    }

    selected_period = period
    if has_from and has_to:
        if from_date > to_date:
            raise HTTPException(status_code=400, detail="from_date to_date dan katta bo'lishi mumkin emas")
        selected_period = "custom"
    else:
        if period not in periods:
            raise HTTPException(
                status_code=400,
                detail="period faqat quyidagilardan biri bo'lishi kerak: 3d, 7d, 15d, 30d"
            )
        today_uz = datetime.now(UZBEKISTAN_TZ).date()
        to_date = today_uz
        from_date = today_uz - timedelta(days=periods[period] - 1)

    start_utc_naive, end_utc_naive = _date_range_uz_to_utc_naive(from_date, to_date)

    base_query = select(customer).where(
        and_(
            customer.c.created_at >= start_utc_naive,
            customer.c.created_at < end_utc_naive,
            customer.c.is_archived.is_not(True)
        )
    )
    if status_filter:
        base_query = base_query.where(customer.c.status == status_filter)

    result = await session.execute(base_query.order_by(desc(customer.c.created_at)))
    customers = result.fetchall()

    customer_items = []
    status_dict: dict[str, int] = {}
    search_term = search.strip().lower() if search and search.strip() else None

    for c in customers:
        decrypted_name = decrypt_text(c.full_name)
        decrypted_phone = decrypt_text(c.phone_number)
        status_key = c.status.value if hasattr(c.status, "value") else str(c.status)

        if search_term:
            if not any([
                search_term in decrypted_name.lower(),
                search_term in decrypted_phone,
                search_term in (c.platform or "").lower(),
                search_term in (c.username or "").lower(),
                search_term in (c.assistant_name or "").lower(),
                search_term in status_key.lower()
            ]):
                continue

        status_dict[status_key] = status_dict.get(status_key, 0) + 1

        customer_items.append(
            CustomerResponse(
                id=c.id,
                full_name=decrypted_name,
                platform=c.platform,
                username=c.username,
                phone_number=decrypted_phone,
                status=status_key,
                assistant_name=c.assistant_name,
                chat_url=c.chat_url,
                notes=c.notes,
                aisummary=c.aisummary,
                audio_file_id=c.audio_file_id,
                conversation_language=c.conversation_language,
                recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
                created_at=c.created_at.isoformat()
            )
        )

    status_stats = {s.value: status_dict.get(s.value, 0) for s in CustomerStatus}

    total = len(customer_items)
    status_percentages: dict[str, float] = {}
    if total > 0:
        for key, count in status_dict.items():
            status_percentages[key] = round((count / total) * 100, 1)

    return CustomerPeriodReportResponse(
        period=selected_period,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        total_customers=total,
        customers=customer_items,
        status_stats=status_stats,
        status_dict=status_dict,
        status_percentages=status_percentages
    )


# 3РїС‘РЏРІС“Р€ SANA BOРІР‚ВYICHA FILTER
@router.get(
    "/customers/filter/date",
    response_model=List[CustomerResponse],
    summary="Sana oraligРІР‚Вiga koРІР‚Вra mijozlarni filterlash"
)
async def filter_customers_by_date(
    start_date: datetime = Query(..., description="Boshlanish sanasi (YYYY-MM-DD)"),
    end_date: datetime = Query(None, description="Tugash sanasi (YYYY-MM-DD)"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_user),
):
    """
    Sana oraligРІР‚Вiga yoki bitta sanaga koРІР‚Вra mijozlarni filterlaydi.
    Agar faqat `start_date` berilsa РІР‚вЂќ oРІР‚Вsha kunlik yozuvlar chiqadi.
    """
  
    # Agar end_date berilmasa, start_date ni oРІР‚Вsha kun deb olamiz
    if not end_date:
        end_date = start_date  # end_date bo'sh bo'lsa, uni start_date ga tenglashtiramiz

    # Sana oralig'ida filtrlaymiz
    result = await session.execute(
        select(customer)
        .where(and_(
            customer.c.created_at >= start_date,
            customer.c.created_at < end_date + timedelta(days=1),
            customer.c.is_archived.is_not(True)
        ))
        .order_by(desc(customer.c.created_at))
    )
    customers = result.fetchall()

    # Mijozlar topilmasa 404 xatolik yuborish
    if not customers:
        raise HTTPException(status_code=404, detail="Berilgan sana oraligРІР‚Вida mijoz topilmadi")

    return [
        CustomerResponse(
            id=c.id,
            full_name=decrypt_text(c.full_name),
            platform=c.platform,
            username=c.username,
            phone_number=decrypt_text(c.phone_number),
            status=c.status.value,
            assistant_name=c.assistant_name,
            chat_url=c.chat_url,
            notes=c.notes,
            aisummary=c.aisummary,
            audio_file_id=c.audio_file_id,
            conversation_language=c.conversation_language,
            recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
            created_at=c.created_at.isoformat()
        )
        for c in customers
    ]

# ─────────────────────────────────────────────
# ARCHIVE / RESTORE / HARD-DELETE ENDPOINTS
# ─────────────────────────────────────────────

def _require_ceo(current_user):
    if current_user.company_code != "ceo":
        raise HTTPException(status_code=403, detail="Bu amal faqat CEO uchun ruxsat etilgan")


@router.get("/customers/archived", response_model=List[CustomerResponse], summary="Arxivlangan mijozlar (CEO only)")
async def get_archived_customers(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    _require_ceo(current_user)
    result = await session.execute(
        select(customer)
        .where(customer.c.is_archived == True)
        .order_by(desc(customer.c.created_at))
    )
    customers_data = result.fetchall()
    return [
        CustomerResponse(
            id=c.id,
            full_name=_safe_decrypt(c.full_name),
            platform=c.platform,
            username=c.username,
            phone_number=_safe_decrypt(c.phone_number),
            status=c.status_name or (c.status.value if hasattr(c.status, "value") else c.status),
            assistant_name=c.assistant_name,
            chat_url=getattr(c, "chat_url", None),
            notes=c.notes,
            aisummary=c.aisummary,
            audio_file_id=c.audio_file_id,
            conversation_language=getattr(c, "conversation_language", None),
            recall_time=_from_utc_naive_to_uz_iso(c.recall_time),
            created_at=c.created_at.isoformat(),
            is_archived=True,
        )
        for c in customers_data
    ]


@router.post("/customers/{customer_id}/restore", response_model=SuccessResponse, summary="Arxivdan tiklash (CEO only)")
async def restore_customer(
    customer_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    _require_ceo(current_user)
    result = await session.execute(select(customer).where(customer.c.id == customer_id))
    existing = result.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    if not getattr(existing, "is_archived", None):
        raise HTTPException(status_code=400, detail="Mijoz arxivlanmagan")
    before_snapshot = _serialize_customer_for_audit(existing)
    await session.execute(
        update(customer).where(customer.c.id == customer_id).values(is_archived=False)
    )
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer",
        entity_id=customer_id,
        action="restore",
        summary=f"Lead arxivdan tiklandi: {before_snapshot['full_name']}",
        actor_user=current_user,
        request=request,
        before_data=before_snapshot,
    )
    await session.commit()
    return SuccessResponse(message=f"Mijoz {before_snapshot['full_name']} muvaffaqiyatli tiklandi")


@router.delete("/customers/{customer_id}/hard", response_model=SuccessResponse, summary="Mijozni butunlay o'chirish (CEO only)")
async def hard_delete_customer(
    customer_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    _require_ceo(current_user)
    result = await session.execute(select(customer).where(customer.c.id == customer_id))
    existing = result.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    before_snapshot = _serialize_customer_for_audit(existing)
    await session.execute(delete(customer).where(customer.c.id == customer_id))
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer",
        entity_id=customer_id,
        action="hard_delete",
        summary=f"Lead butunlay o'chirildi: {before_snapshot['full_name']}",
        actor_user=current_user,
        request=request,
        before_data=before_snapshot,
    )
    await session.commit()
    await _delete_customer_calendar_best_effort(customer_id)
    return SuccessResponse(message=f"Mijoz {before_snapshot['full_name']} butunlay o'chirildi")


@router.delete("/customers/bulk-hard-delete", response_model=SuccessResponse, summary="Ko'p mijozlarni butunlay o'chirish (CEO only)")
async def bulk_hard_delete_customers(
    delete_data: CustomerDeleteRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_crm_access),
):
    _require_ceo(current_user)
    if not delete_data.customer_ids:
        raise HTTPException(status_code=400, detail="O'chiriladigan mijozlar ro'yxati bo'sh")
    customers_result = await session.execute(
        select(customer).where(customer.c.id.in_(delete_data.customer_ids))
    )
    existing_customers = customers_result.fetchall()
    existing_customer_ids = [row.id for row in existing_customers]
    await session.execute(
        delete(customer).where(customer.c.id.in_(delete_data.customer_ids))
    )
    for existing_customer in existing_customers:
        before_snapshot = _serialize_customer_for_audit(existing_customer)
        await log_audit_event(
            session,
            module="crm",
            table_name="customer",
            entity_type="customer",
            entity_id=existing_customer.id,
            action="hard_delete",
            summary=f"Lead bulk hard delete qilindi: {before_snapshot['full_name']}",
            actor_user=current_user,
            request=request,
            before_data=before_snapshot,
        )
    await log_audit_event(
        session,
        module="crm",
        table_name="customer",
        entity_type="customer_bulk",
        entity_id=None,
        action="bulk_hard_delete",
        summary=f"{len(existing_customer_ids)} ta lead bulk hard delete qilindi",
        actor_user=current_user,
        request=request,
        before_data={"customer_ids": existing_customer_ids},
    )
    await session.commit()
    for customer_id in existing_customer_ids:
        await _delete_customer_calendar_best_effort(customer_id)
    return SuccessResponse(message=f"{len(existing_customer_ids)} ta mijoz butunlay o'chirildi")
