"""
CRM Sales Manager Extension
Sales Manager assignment and conversion rate tracking
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete, func, desc
from datetime import datetime
from typing import List

from database import get_async_session
from auth_utils.auth_func import get_current_active_user
from models.user_models import user, UserRole
from models.admin_models import (
    customer,
    sales_manager_assignment,
    sales_manager_counter,
    CustomerStatus
)
from schemes.schemes_management import (
    SalesManagerAssignmentCreate,
    SalesManagerAssignmentResponse,
    SalesManagerInfo,
    ConversionRateResponse
)

router = APIRouter(prefix="/crm", tags=["CRM - Sales Manager"])


# ========================================
# HELPER FUNCTIONS
# ========================================

async def get_next_sales_manager(session: AsyncSession) -> int:
    """
    Round-robin: Get next sales manager ID for auto-assignment
    Returns the user_id of the next sales manager
    """
    # Get all active sales managers
    result = await session.execute(
        select(user.c.id)
        .where(
            (user.c.role == UserRole.sales_manager) &
            (user.c.is_active == True)
        )
        .order_by(user.c.id)
    )
    sales_managers = result.fetchall()

    if not sales_managers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Faol Sales Manager topilmadi"
        )

    # Get current counter
    counter_result = await session.execute(select(sales_manager_counter))
    counter_row = counter_result.fetchone()

    if not counter_row:
        # Initialize counter if not exists
        await session.execute(
            insert(sales_manager_counter).values(
                last_assigned_index=0,
                updated_at=datetime.utcnow()
            )
        )
        await session.commit()
        next_index = 0
    else:
        next_index = (counter_row.last_assigned_index + 1) % len(sales_managers)

    # Update counter
    await session.execute(
        update(sales_manager_counter)
        .values(
            last_assigned_index=next_index,
            updated_at=datetime.utcnow()
        )
    )
    await session.commit()

    return sales_managers[next_index].id


async def auto_assign_sales_manager(customer_id: int, session: AsyncSession) -> int:
    """
    Automatically assign a sales manager to a customer using round-robin
    Returns the assigned sales_manager_id
    """
    # Check if already assigned
    existing = await session.execute(
        select(sales_manager_assignment)
        .where(
            (sales_manager_assignment.c.customer_id == customer_id) &
            (sales_manager_assignment.c.is_active == True)
        )
    )
    if existing.fetchone():
        return None  # Already assigned

    # Get next sales manager
    sales_manager_id = await get_next_sales_manager(session)

    # Create assignment
    await session.execute(
        insert(sales_manager_assignment).values(
            customer_id=customer_id,
            sales_manager_id=sales_manager_id,
            assigned_at=datetime.utcnow(),
            assigned_by=None,  # Auto-assigned
            is_active=True
        )
    )
    await session.commit()

    return sales_manager_id


# ========================================
# SALES MANAGER ENDPOINTS
# ========================================

@router.get("/sales-managers", response_model=List[SalesManagerInfo], summary="Barcha Sales Managerlarni ko'rish")
async def get_sales_managers(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Barcha faol Sales Managerlarni va ularning assign qilingan mijozlar sonini ko'rsatish
    """
    # Get all sales managers with their assignment counts
    result = await session.execute(
        select(
            user.c.id,
            user.c.email,
            user.c.name,
            user.c.surname,
            func.count(sales_manager_assignment.c.id).label('assigned_leads_count')
        )
        .outerjoin(
            sales_manager_assignment,
            (user.c.id == sales_manager_assignment.c.sales_manager_id) &
            (sales_manager_assignment.c.is_active == True)
        )
        .where(
            (user.c.role == UserRole.sales_manager) &
            (user.c.is_active == True)
        )
        .group_by(user.c.id, user.c.email, user.c.name, user.c.surname)
        .order_by(user.c.name)
    )

    managers = result.fetchall()

    return [
        SalesManagerInfo(
            id=m.id,
            email=m.email,
            name=m.name,
            surname=m.surname,
            assigned_leads_count=m.assigned_leads_count
        )
        for m in managers
    ]


@router.post("/assign-sales-manager", response_model=SalesManagerAssignmentResponse, summary="Sales Manager assign qilish")
async def assign_sales_manager_to_customer(
    assignment_data: SalesManagerAssignmentCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Mijozga Sales Manager qo'lda assign qilish (CEO yoki boshqa authorized user)
    """
    # Verify customer exists
    customer_result = await session.execute(
        select(customer).where(customer.c.id == assignment_data.customer_id)
    )
    if not customer_result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mijoz topilmadi"
        )

    # Verify sales manager exists and is active
    sm_result = await session.execute(
        select(user).where(
            (user.c.id == assignment_data.sales_manager_id) &
            (user.c.role == UserRole.sales_manager) &
            (user.c.is_active == True)
        )
    )
    if not sm_result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sales Manager topilmadi yoki faol emas"
        )

    # Check if already assigned
    existing = await session.execute(
        select(sales_manager_assignment)
        .where(
            (sales_manager_assignment.c.customer_id == assignment_data.customer_id) &
            (sales_manager_assignment.c.is_active == True)
        )
    )
    existing_assignment = existing.fetchone()

    if existing_assignment:
        # Update existing assignment
        await session.execute(
            update(sales_manager_assignment)
            .where(sales_manager_assignment.c.id == existing_assignment.id)
            .values(
                sales_manager_id=assignment_data.sales_manager_id,
                assigned_by=current_user.id,
                assigned_at=datetime.utcnow()
            )
        )
        await session.commit()

        return SalesManagerAssignmentResponse(
            id=existing_assignment.id,
            customer_id=assignment_data.customer_id,
            sales_manager_id=assignment_data.sales_manager_id,
            assigned_at=datetime.utcnow(),
            assigned_by=current_user.id,
            is_active=True
        )
    else:
        # Create new assignment
        insert_stmt = insert(sales_manager_assignment).values(
            customer_id=assignment_data.customer_id,
            sales_manager_id=assignment_data.sales_manager_id,
            assigned_at=datetime.utcnow(),
            assigned_by=current_user.id,
            is_active=True
        ).returning(sales_manager_assignment)

        result = await session.execute(insert_stmt)
        await session.commit()
        new_assignment = result.fetchone()

        return SalesManagerAssignmentResponse(
            id=new_assignment.id,
            customer_id=new_assignment.customer_id,
            sales_manager_id=new_assignment.sales_manager_id,
            assigned_at=new_assignment.assigned_at,
            assigned_by=new_assignment.assigned_by,
            is_active=new_assignment.is_active
        )


@router.get("/customer/{customer_id}/sales-manager", summary="Mijozning Sales Managerini ko'rish")
async def get_customer_sales_manager(
    customer_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Mijozga assign qilingan Sales Managerni ko'rish
    """
    result = await session.execute(
        select(
            sales_manager_assignment.c.id,
            sales_manager_assignment.c.assigned_at,
            sales_manager_assignment.c.assigned_by,
            user.c.id.label('sm_id'),
            user.c.email,
            user.c.name,
            user.c.surname
        )
        .join(user, user.c.id == sales_manager_assignment.c.sales_manager_id)
        .where(
            (sales_manager_assignment.c.customer_id == customer_id) &
            (sales_manager_assignment.c.is_active == True)
        )
    )
    assignment = result.fetchone()

    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bu mijozga Sales Manager assign qilinmagan"
        )

    return {
        "assignment_id": assignment.id,
        "customer_id": customer_id,
        "sales_manager": {
            "id": assignment.sm_id,
            "email": assignment.email,
            "name": assignment.name,
            "surname": assignment.surname
        },
        "assigned_at": assignment.assigned_at,
        "assigned_by": assignment.assigned_by
    }


# ========================================
# CONVERSION RATE ENDPOINT
# ========================================

@router.get("/conversion-rate", response_model=ConversionRateResponse, summary="Conversion rate ko'rish")
async def get_conversion_rate(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Oxirgi 100 ta leaddan nechta 'project_started' statusiga o'tganini hisoblash
    Conversion rate = (project_started count / total count) * 100
    """
    # Get last 100 customers ordered by created_at
    result = await session.execute(
        select(customer.c.status, customer.c.status_name)
        .order_by(desc(customer.c.created_at))
        .limit(100)
    )
    customers = result.fetchall()

    if not customers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mijozlar topilmadi"
        )

    total_count = len(customers)

    # Count customers with 'project_started' status (either enum or dynamic)
    project_started_count = sum(
        1 for c in customers
        if (c.status == CustomerStatus.project_started) or
           (c.status_name == 'project_started')
    )

    # Calculate conversion rate
    conversion_rate = (project_started_count / total_count * 100) if total_count > 0 else 0.0

    return ConversionRateResponse(
        total_customers=total_count,
        project_started_count=project_started_count,
        conversion_rate=round(conversion_rate, 2),
        period=f"Oxirgi {total_count} ta lead"
    )


# ========================================
# EXPORT FUNCTION FOR CRM ROUTER
# ========================================

async def maybe_auto_assign_sales_manager(customer_id: int, session: AsyncSession):
    """
    Call this function after creating a new customer to auto-assign a sales manager
    This is meant to be called from the main CRM router
    """
    try:
        await auto_assign_sales_manager(customer_id, session)
    except HTTPException:
        # No sales managers available - skip auto-assignment
        pass
