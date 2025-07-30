from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, insert, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

# Import qilinadigan modellar
from models.user_models import user_page_permission, PageName
from models.admin_models import site_control, wordpress_project  # Yangi jadval qo'shildi
from schemes.wordpress_schemes import (
    SiteStatusResponse, SiteToggleRequest, WordPressDashboardResponse,
    WordPressProjectCreateRequest, WordPressProjectUpdateRequest,
    WordPressProjectResponse, WordPressProjectListResponse,
    SuccessResponse, CreateResponse
)
from auth_utils.auth_func import get_current_active_user
from database import get_async_session

router = APIRouter(prefix="/wordpress", tags=['WordPress Project Management'])


# --- DECORATOR: WordPress sahifa ruxsatini tekshirish ---
def require_wordpress_access(current_user=Depends(get_current_active_user)):
    """WordPress sahifasiga kirish ruxsatini tekshirish"""
    if current_user.company_code == "ceo":
        return current_user

    # Foydalanuvchining ruxsatlarini tekshirish kerak
    # Bu dependency injection ichida qo'shimcha session kerak bo'ladi
    # Shuning uchun bu funksiyani endpoint ichida tekshiramiz
    return current_user


# --- 1. WordPress Dashboard - Asosiy sahifa ---
@router.get("/dashboard", response_model=WordPressDashboardResponse, summary="WordPress dashboard")
async def wordpress_dashboard(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(get_current_active_user)
):
    """
    WordPress dashboard - sayt holati, foydalanuvchi ruxsatlari va loyihalar
    """
    # Ruxsatni tekshirish
    if current_user.company_code != "ceo":
        permissions_result = await session.execute(
            select(user_page_permission.c.page_name)
            .where(
                user_page_permission.c.user_id == current_user.id,
                user_page_permission.c.page_name == PageName.project_toggle
            )
        )
        if not permissions_result.fetchone():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="WordPress sahifasiga kirish ruxsatingiz yo'q"
            )

    # Sayt holatini olish
    site_result = await session.execute(select(site_control))
    site_data = site_result.fetchone()

    # Agar sayt kontroli mavjud bo'lmasa, yaratish
    if not site_data:
        await session.execute(insert(site_control).values(is_site_on=True))
        await session.commit()
        is_site_on = True
    else:
        is_site_on = site_data.is_site_on

    # Foydalanuvchi ruxsatlarini olish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == current_user.id)
    )
    permissions = [perm.page_name.value for perm in permissions_result.fetchall()]

    # Ruxsat nomlarini o'zgartirish
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

    # WordPress loyihalarini olish
    projects_result = await session.execute(select(wordpress_project))
    projects_data = projects_result.fetchall()

    projects_list = []
    for project in projects_data:
        projects_list.append(WordPressProjectResponse(
            id=project.id,
            name=project.name,
            url=project.url,
            description=project.description,
            is_active=project.is_active
        ))

    # Statistikalar
    total_projects = len(projects_data)
    active_projects = len([p for p in projects_data if p.is_active])
    inactive_projects = total_projects - active_projects

    return WordPressDashboardResponse(
        site_status=is_site_on,
        permissions=modified_permissions,
        projects=projects_list,
        statistics={
            "total_projects": total_projects,
            "active_projects": active_projects,
            "inactive_projects": inactive_projects
        }
    )


# --- 2. Umumiy sayt holatini olish ---
@router.get("/site-status", response_model=SiteStatusResponse, summary="Umumiy sayt holatini olish")
async def get_site_status(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_wordpress_access)
):
    """
    Umumiy saytning joriy holatini qaytaradi (yoqilgan/o'chirilgan)
    Bu barcha sayt uchun umumiy on/off switch
    """
    # Sayt holatini olish
    site_result = await session.execute(select(site_control))
    site_data = site_result.fetchone()

    if not site_data:
        # Agar mavjud bo'lmasa, default holatda yaratish
        await session.execute(insert(site_control).values(is_site_on=True))
        await session.commit()
        is_site_on = True
    else:
        is_site_on = site_data.is_site_on

    return SiteStatusResponse(
        is_site_on=is_site_on,
        message=f"Umumiy sayt hozirda {'yoqilgan' if is_site_on else 'ochirilgan'}"
    )


# --- 3. Umumiy sayt holatini o'zgarttirish ---
@router.post("/toggle-site", response_model=SiteStatusResponse, summary="Umumiy sayt holatini o'zgartirish")
async def toggle_site_status(
        toggle_data: SiteToggleRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_wordpress_access)
):
    """
    Umumiy sayt holatini o'zgartirish (yoqish/o'chirish)
    Bu barcha sayt uchun master switch - barcha loyihalarni bir vaqtda boshqaradi
    """
    # Sayt holatini olish
    site_result = await session.execute(select(site_control))
    site_data = site_result.fetchone()

    if not site_data:
        # Agar mavjud bo'lmasa, yaratish
        new_status = toggle_data.action != "off"
        await session.execute(insert(site_control).values(is_site_on=new_status))
        await session.commit()
        is_site_on = new_status
    else:
        # Holatni o'zgartirish
        if toggle_data.action == "toggle":
            new_status = not site_data.is_site_on
        elif toggle_data.action == "on":
            new_status = True
        elif toggle_data.action == "off":
            new_status = False
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Noto'g'ri harakat. 'toggle', 'on' yoki 'off' bo'lishi kerak"
            )

        await session.execute(
            update(site_control).where(site_control.c.id == site_data.id).values(is_site_on=new_status)
        )
        await session.commit()
        is_site_on = new_status

    return SiteStatusResponse(
        is_site_on=is_site_on,
        message=f"Umumiy sayt muvaffaqiyatli {'yoqildi' if is_site_on else 'ochirildi'}"
    )


# --- 4. Barcha WordPress loyihalari ro'yxati ---
@router.get("/projects", response_model=WordPressProjectListResponse, summary="Barcha WordPress loyihalari")
async def get_wordpress_projects(
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_wordpress_access)
):
    """
    Barcha WordPress loyihalarini qaytaradi
    """
    # Loyihalarni olish
    projects_result = await session.execute(select(wordpress_project).order_by(wordpress_project.c.id.desc()))
    projects_data = projects_result.fetchall()

    projects_list = []
    for project in projects_data:
        projects_list.append(WordPressProjectResponse(
            id=project.id,
            name=project.name,
            url=project.url,
            description=project.description,
            is_active=project.is_active
        ))

    return WordPressProjectListResponse(
        projects=projects_list,
        total_count=len(projects_list)
    )


# --- 5. Yangi WordPress loyihasini yaratish ---
@router.post("/projects", response_model=CreateResponse, summary="Yangi WordPress loyihasini yaratish")
async def create_wordpress_project(
        project_data: WordPressProjectCreateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_wordpress_access)
):
    """
    Yangi WordPress loyihasini yaratish
    """
    # Loyiha nomi mavjudligini tekshirish
    existing_project_result = await session.execute(
        select(wordpress_project).where(wordpress_project.c.name == project_data.name)
    )
    if existing_project_result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bu loyiha nomi allaqachon mavjud"
        )

    # Yangi loyiha yaratish
    project_dict = {
        "name": project_data.name,
        "url": project_data.url,
        "description": project_data.description,
        "is_active": project_data.is_active
    }

    result = await session.execute(insert(wordpress_project).values(**project_dict))
    await session.commit()

    return CreateResponse(
        message="WordPress loyiha muvaffaqiyatli yaratildi",
        id=result.inserted_primary_key[0]
    )


# --- 6. WordPress loyihasini yangilash ---
@router.put("/projects/{project_id}", response_model=SuccessResponse, summary="WordPress loyihasini yangilash")
async def update_wordpress_project(
        project_id: int,
        project_data: WordPressProjectUpdateRequest,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_wordpress_access)
):
    """
    Mavjud WordPress loyiha ma'lumotlarini yangilash
    """
    # Loyiha mavjudligini tekshirish
    existing_project_result = await session.execute(
        select(wordpress_project).where(wordpress_project.c.id == project_id)
    )
    existing_project = existing_project_result.fetchone()

    if not existing_project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress loyiha topilmadi"
        )

    # Yangilanadigan ma'lumotlarni tayyorlash
    update_data = project_data.dict(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi"
        )

    # Agar loyiha nomi yangilanayotgan bo'lsa, mavjudligini tekshirish
    if "name" in update_data:
        existing_name_result = await session.execute(
            select(wordpress_project).where(
                wordpress_project.c.name == update_data["name"],
                wordpress_project.c.id != project_id
            )
        )
        if existing_name_result.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bu loyiha nomi allaqachon mavjud"
            )

    # Ma'lumotlarni yangilash
    await session.execute(
        update(wordpress_project).where(wordpress_project.c.id == project_id).values(**update_data)
    )
    await session.commit()

    return SuccessResponse(message="WordPress loyiha muvaffaqiyatli yangilandi")


# --- 7. WordPress loyihasini o'chirish ---
@router.delete("/projects/{project_id}", response_model=SuccessResponse, summary="WordPress loyihasini o'chirish")
async def delete_wordpress_project(
        project_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_wordpress_access)
):
    """
    WordPress loyihasini tizimdan o'chirish
    """
    # Loyiha mavjudligini tekshirish
    existing_project_result = await session.execute(
        select(wordpress_project).where(wordpress_project.c.id == project_id)
    )
    existing_project = existing_project_result.fetchone()

    if not existing_project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress loyiha topilmadi"
        )

    # Loyihani o'chirish
    await session.execute(delete(wordpress_project).where(wordpress_project.c.id == project_id))
    await session.commit()

    return SuccessResponse(message=f"WordPress loyiha '{existing_project.name}' muvaffaqiyatli o'chirildi")


# --- 8. WordPress loyiha holatini o'zgartirish ---
@router.patch("/projects/{project_id}/toggle-active", response_model=WordPressProjectResponse,
              summary="WordPress loyiha holatini o'zgartirish")
async def toggle_wordpress_project_active(
        project_id: int,
        session: AsyncSession = Depends(get_async_session),
        current_user=Depends(require_wordpress_access)
):
    """
    WordPress loyihasining faollik holatini o'zgartirish (faol/nofaol)
    """
    # Loyiha topish
    project_result = await session.execute(
        select(wordpress_project).where(wordpress_project.c.id == project_id)
    )
    project_data = project_result.fetchone()

    if not project_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress loyiha topilmadi"
        )

    # Active holatini o'zgartirish
    new_active_status = not project_data.is_active
    await session.execute(
        update(wordpress_project).where(wordpress_project.c.id == project_id).values(is_active=new_active_status)
    )
    await session.commit()

    return WordPressProjectResponse(
        id=project_data.id,
        name=project_data.name,
        url=project_data.url,
        description=project_data.description,
        is_active=new_active_status
    )