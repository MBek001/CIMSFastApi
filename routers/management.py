"""
Management Router - Status and Role Management APIs
CEO can manage customer statuses and user roles dynamically
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete, func
from datetime import datetime

from database import get_async_session
from auth_utils.auth_func import get_current_active_user
from models.user_models import user, UserRole
from models.admin_models import (
    customer_status_table,
    user_role_table,
    customer
)
from schemes.schemes_management import (
    CustomerStatusCreate,
    CustomerStatusUpdate,
    CustomerStatusResponse,
    UserRoleCreate,
    UserRoleUpdate,
    UserRoleResponse,
)

router = APIRouter(prefix="/management", tags=["Management"])


# ========================================
# HELPER FUNCTIONS
# ========================================

async def require_ceo_access(
    current_user=Depends(get_current_active_user)
):
    """Faqat CEO kirishi mumkin"""
    if current_user.role != UserRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Faqat CEO bu operatsiyani bajara oladi"
        )
    return current_user


async def initialize_default_statuses(session: AsyncSession):
    """Initialize default customer statuses if table is empty"""
    result = await session.execute(select(func.count()).select_from(customer_status_table))
    count = result.scalar()

    if count == 0:
        default_statuses = [
            {"name": "contacted", "display_name": "Contacted", "description": "Initial contact made", "color": "#3B82F6", "order": 1, "is_system": True},
            {"name": "project_started", "display_name": "Project Started", "description": "Project has started", "color": "#10B981", "order": 2, "is_system": True},
            {"name": "continuing", "display_name": "Continuing", "description": "Project is continuing", "color": "#F59E0B", "order": 3, "is_system": True},
            {"name": "finished", "display_name": "Finished", "description": "Project completed", "color": "#8B5CF6", "order": 4, "is_system": True},
            {"name": "rejected", "display_name": "Rejected", "description": "Lead rejected", "color": "#EF4444", "order": 5, "is_system": True},
            {"name": "need_to_call", "display_name": "Need to Call", "description": "Follow-up call needed", "color": "#F97316", "order": 6, "is_system": True},
        ]

        for status_data in default_statuses:
            await session.execute(insert(customer_status_table).values(**status_data))
        await session.commit()


async def initialize_default_roles(session: AsyncSession):
    """Initialize default user roles if table is empty"""
    result = await session.execute(select(func.count()).select_from(user_role_table))
    count = result.scalar()

    if count == 0:
        default_roles = [
            {"name": "ceo", "display_name": "CEO", "description": "Chief Executive Officer", "is_system": True},
            {"name": "financial_director", "display_name": "Financial Director", "description": "Financial Director", "is_system": True},
            {"name": "sales_manager", "display_name": "Sales Manager", "description": "Sales Manager for CRM", "is_system": True},
            {"name": "member", "display_name": "Member", "description": "Team Member", "is_system": True},
            {"name": "customer", "display_name": "Customer", "description": "Customer/Client", "is_system": True},
        ]

        for role_data in default_roles:
            await session.execute(insert(user_role_table).values(**role_data))
        await session.commit()


# ========================================
# CUSTOMER STATUS ENDPOINTS
# ========================================

@router.get("/statuses", response_model=list[CustomerStatusResponse], summary="Barcha statuslarni ko'rish")
async def get_all_statuses(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Barcha mijoz statuslarini olish (active va inactive)
    """
    # Initialize defaults if needed
    await initialize_default_statuses(session)

    result = await session.execute(
        select(customer_status_table).order_by(customer_status_table.c.order)
    )
    statuses = result.fetchall()

    return [
        CustomerStatusResponse(
            id=s.id,
            name=s.name,
            display_name=s.display_name,
            description=s.description,
            color=s.color,
            order=s.order,
            is_active=s.is_active,
            is_system=s.is_system,
            created_at=s.created_at,
            updated_at=s.updated_at
        )
        for s in statuses
    ]


@router.post("/statuses", response_model=CustomerStatusResponse, summary="Yangi status yaratish")
async def create_status(
    status_data: CustomerStatusCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Yangi mijoz statusini yaratish (faqat CEO)
    """
    # Check if status name already exists
    result = await session.execute(
        select(customer_status_table).where(customer_status_table.c.name == status_data.name)
    )
    existing = result.fetchone()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Status '{status_data.name}' allaqachon mavjud"
        )

    # Create new status
    insert_stmt = insert(customer_status_table).values(
        name=status_data.name,
        display_name=status_data.display_name,
        description=status_data.description,
        color=status_data.color,
        order=status_data.order,
        is_active=status_data.is_active,
        is_system=status_data.is_system,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    ).returning(customer_status_table)

    result = await session.execute(insert_stmt)
    await session.commit()
    new_status = result.fetchone()

    return CustomerStatusResponse(
        id=new_status.id,
        name=new_status.name,
        display_name=new_status.display_name,
        description=new_status.description,
        color=new_status.color,
        order=new_status.order,
        is_active=new_status.is_active,
        is_system=new_status.is_system,
        created_at=new_status.created_at,
        updated_at=new_status.updated_at
    )


@router.put("/statuses/{status_id}", response_model=CustomerStatusResponse, summary="Statusni yangilash")
async def update_status(
    status_id: int,
    status_data: CustomerStatusUpdate,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Mavjud statusni yangilash (faqat CEO)
    """
    # Check if status exists
    result = await session.execute(
        select(customer_status_table).where(customer_status_table.c.id == status_id)
    )
    existing_status = result.fetchone()

    if not existing_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Status topilmadi"
        )

    # Prepare update data
    update_data = {k: v for k, v in status_data.model_dump(exclude_unset=True).items() if v is not None}
    update_data['updated_at'] = datetime.utcnow()

    if not update_data or len(update_data) == 1:  # Only updated_at
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hech qanday yangilanish ma'lumoti berilmagan"
        )

    # Update status
    update_stmt = (
        update(customer_status_table)
        .where(customer_status_table.c.id == status_id)
        .values(**update_data)
        .returning(customer_status_table)
    )

    result = await session.execute(update_stmt)
    await session.commit()
    updated_status = result.fetchone()

    return CustomerStatusResponse(
        id=updated_status.id,
        name=updated_status.name,
        display_name=updated_status.display_name,
        description=updated_status.description,
        color=updated_status.color,
        order=updated_status.order,
        is_active=updated_status.is_active,
        is_system=updated_status.is_system,
        created_at=updated_status.created_at,
        updated_at=updated_status.updated_at
    )


@router.delete("/statuses/{status_id}", summary="Statusni o'chirish")
async def delete_status(
    status_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Statusni o'chirish (faqat CEO, system statuslarni o'chirish mumkin emas)
    """
    # Check if status exists
    result = await session.execute(
        select(customer_status_table).where(customer_status_table.c.id == status_id)
    )
    existing_status = result.fetchone()

    if not existing_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Status topilmadi"
        )

    if existing_status.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System statuslarni o'chirish mumkin emas"
        )

    # Check if any customers are using this status
    result = await session.execute(
        select(func.count()).select_from(customer).where(customer.c.status_name == existing_status.name)
    )
    usage_count = result.scalar()

    if usage_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bu status {usage_count} ta mijozda ishlatilmoqda. Avval ularni boshqa statusga o'zgartiring"
        )

    # Delete status
    await session.execute(
        delete(customer_status_table).where(customer_status_table.c.id == status_id)
    )
    await session.commit()

    return {"message": f"Status '{existing_status.display_name}' muvaffaqiyatli o'chirildi"}


# ========================================
# USER ROLE ENDPOINTS
# ========================================

@router.get("/roles", response_model=list[UserRoleResponse], summary="Barcha rollarni ko'rish")
async def get_all_roles(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Barcha foydalanuvchi rollarini olish (active va inactive)
    """
    # Initialize defaults if needed
    await initialize_default_roles(session)

    result = await session.execute(
        select(user_role_table).order_by(user_role_table.c.display_name)
    )
    roles = result.fetchall()

    return [
        UserRoleResponse(
            id=r.id,
            name=r.name,
            display_name=r.display_name,
            description=r.description,
            is_active=r.is_active,
            is_system=r.is_system,
            created_at=r.created_at,
            updated_at=r.updated_at
        )
        for r in roles
    ]


@router.post("/roles", response_model=UserRoleResponse, summary="Yangi rol yaratish")
async def create_role(
    role_data: UserRoleCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Yangi foydalanuvchi rolini yaratish (faqat CEO)
    """
    # Check if role name already exists
    result = await session.execute(
        select(user_role_table).where(user_role_table.c.name == role_data.name)
    )
    existing = result.fetchone()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rol '{role_data.name}' allaqachon mavjud"
        )

    # Create new role
    insert_stmt = insert(user_role_table).values(
        name=role_data.name,
        display_name=role_data.display_name,
        description=role_data.description,
        is_active=role_data.is_active,
        is_system=role_data.is_system,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    ).returning(user_role_table)

    result = await session.execute(insert_stmt)
    await session.commit()
    new_role = result.fetchone()

    return UserRoleResponse(
        id=new_role.id,
        name=new_role.name,
        display_name=new_role.display_name,
        description=new_role.description,
        is_active=new_role.is_active,
        is_system=new_role.is_system,
        created_at=new_role.created_at,
        updated_at=new_role.updated_at
    )


@router.put("/roles/{role_id}", response_model=UserRoleResponse, summary="Rolni yangilash")
async def update_role(
    role_id: int,
    role_data: UserRoleUpdate,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Mavjud rolni yangilash (faqat CEO)
    """
    # Check if role exists
    result = await session.execute(
        select(user_role_table).where(user_role_table.c.id == role_id)
    )
    existing_role = result.fetchone()

    if not existing_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rol topilmadi"
        )

    # Prepare update data
    update_data = {k: v for k, v in role_data.model_dump(exclude_unset=True).items() if v is not None}
    update_data['updated_at'] = datetime.utcnow()

    if not update_data or len(update_data) == 1:  # Only updated_at
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hech qanday yangilanish ma'lumoti berilmagan"
        )

    # Update role
    update_stmt = (
        update(user_role_table)
        .where(user_role_table.c.id == role_id)
        .values(**update_data)
        .returning(user_role_table)
    )

    result = await session.execute(update_stmt)
    await session.commit()
    updated_role = result.fetchone()

    return UserRoleResponse(
        id=updated_role.id,
        name=updated_role.name,
        display_name=updated_role.display_name,
        description=updated_role.description,
        is_active=updated_role.is_active,
        is_system=updated_role.is_system,
        created_at=updated_role.created_at,
        updated_at=updated_role.updated_at
    )


@router.delete("/roles/{role_id}", summary="Rolni o'chirish")
async def delete_role(
    role_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_ceo_access)
):
    """
    Rolni o'chirish (faqat CEO, system rollarni o'chirish mumkin emas)
    """
    # Check if role exists
    result = await session.execute(
        select(user_role_table).where(user_role_table.c.id == role_id)
    )
    existing_role = result.fetchone()

    if not existing_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rol topilmadi"
        )

    if existing_role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System rollarni o'chirish mumkin emas"
        )

    # Check if any users are using this role
    result = await session.execute(
        select(func.count()).select_from(user).where(user.c.role_name == existing_role.name)
    )
    usage_count = result.scalar()

    if usage_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bu rol {usage_count} ta foydalanuvchida ishlatilmoqda. Avval ularni boshqa rolga o'zgartiring"
        )

    # Delete role
    await session.execute(
        delete(user_role_table).where(user_role_table.c.id == role_id)
    )
    await session.commit()

    return {"message": f"Rol '{existing_role.display_name}' muvaffaqiyatli o'chirildi"}
