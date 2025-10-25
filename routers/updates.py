
from datetime import date
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete
from models.user_models import user_page_permission
from models.user_models import monthly_update
from database import get_async_session
from auth_utils.auth_func import get_current_active_user


router = APIRouter(prefix="/members", tags=['Employess Api'])


# 🔹 1. CREATE — Yangi oy ma’lumotini kiritish (faqat update_list permission bilan)
@router.post("/member/update", summary="Member uchun yangi oylik ma'lumot kiritish")
async def add_member_update(
    user_id: int,
    year: int,
    month: str,
    update_percentage: float,
    salary_amount: float,
    next_payment_date: date,
    note:str,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):

    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    new_update = {
        "user_id": user_id,
        "year": year,
        "month": month,
        "update_date": date.today(),
        "update_percentage": update_percentage,
        "salary_amount": salary_amount,
        "next_payment_date": next_payment_date,
        "note": note,
    }

    await session.execute(insert(monthly_update).values(**new_update))
    await session.commit()
    return {"message": f"{month}/{year} uchun update muvaffaqiyatli qo‘shildi"}


# 🔹 2. GET — Hamma foydalanuvchilar uchun barcha update’lar (faqat update_list sahifasiga ruxsati borlar uchun)
@router.get("/member/updates/all", summary="Barcha foydalanuvchilarning update'larini olish (ruxsat bilan)")
async def get_all_updates(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    # 🔐 "update_list" sahifasiga ruxsati borligini tekshirish
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga kirish huquqingiz yo‘q")

    # 🔍 Barcha foydalanuvchilarning update’larini olish
    result = await session.execute(select(monthly_update))
    updates = result.fetchall()

    if not updates:
        return {"message": "Hech qanday update topilmadi", "data": []}

    return [
        {
            "id": u.id,
            "user_id": u.user_id,
            "year": u.year,
            "month": u.month,
            "update_date": u.update_date,
            "update_percentage": float(u.update_percentage),
            "salary_amount": float(u.salary_amount),
            "next_payment_date": u.next_payment_date,
            "note":u.note,
        }
        for u in updates
    ]



# 🔹 3. GET — Foydalanuvchining o‘z update’larini ko‘rish
@router.get("/member/updates", summary="Foydalanuvchining o‘z update’larini olish")
async def get_member_updates(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    result = await session.execute(
        select(monthly_update).where(monthly_update.c.user_id == current_user.id)
    )
    updates = result.fetchall()
    return [
        {
            "id": u.id,
            "year": u.year,
            "month": u.month,
            "update_date": u.update_date,
            "update_percentage": float(u.update_percentage),
            "salary_amount": float(u.salary_amount),
            "next_payment_date": u.next_payment_date,
            "note":u.note,
        }
        for u in updates
    ]


# 🔹 4. PUT — To‘liq update’ni tahrirlash (faqat ruxsat bilan)
@router.put("/member/update/{update_id}", summary="Update’ni tahrirlash (to‘liq)")
async def edit_update(
    update_id: int,
    year: int,
    month: str,
    update_percentage: float,
    salary_amount: float,
    next_payment_date: date,
    note:str,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    update_data = {
        "year": year,
        "month": month,
        "update_percentage": update_percentage,
        "salary_amount": salary_amount,
        "next_payment_date": next_payment_date,
        "note": note,
    }

    result = await session.execute(
        update(monthly_update).where(monthly_update.c.id == update_id).values(**update_data)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Update topilmadi")

    return {"message": "Update muvaffaqiyatli tahrirlandi"}


# 🔹 5. PATCH — Qisman yangilash (faqat ruxsat bilan)
@router.patch("/member/update/{update_id}", summary="Update’ni qisman yangilash")
async def patch_update(
    update_id: int,
    update_percentage: float = None,
    salary_amount: float = None,
    next_payment_date: date = None,
    note: str = None,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    update_data = {}
    if update_percentage is not None:
        update_data["update_percentage"] = update_percentage
    if salary_amount is not None:
        update_data["salary_amount"] = salary_amount
    if next_payment_date is not None:
        update_data["next_payment_date"] = next_payment_date
    if note is not None:
        update_data["note"] = note

    if not update_data:
        raise HTTPException(status_code=400, detail="Yangilanadigan maydon topilmadi")

    result = await session.execute(
        update(monthly_update).where(monthly_update.c.id == update_id).values(**update_data)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Update topilmadi")

    return {"message": "Update ma'lumotlari yangilandi"}


# 🔹 6. DELETE — Update’ni o‘chirish (faqat ruxsat bilan)
@router.delete("/member/update/{update_id}", summary="Update’ni o‘chirish")
async def delete_update(
    update_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    permission_check = await session.execute(
        select(user_page_permission).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == "update_list"
        )
    )
    if not permission_check.fetchone():
        raise HTTPException(status_code=403, detail="Bu sahifaga ruxsatingiz yo‘q")

    result = await session.execute(
        delete(monthly_update).where(monthly_update.c.id == update_id)
    )
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Update topilmadi")

    return {"message": "Update muvaffaqiyatli o‘chirildi"}
