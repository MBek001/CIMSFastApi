"""
CRM Endpoint Extension - Dynamic Status Support
Adds endpoint to get dynamic statuses for customer creation
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from database import get_async_session
from auth_utils.auth_func import get_current_active_user
from models.admin_models import customer_status_table

router = APIRouter(prefix="/crm", tags=["CRM"])


class DynamicStatusResponse:
    """Dynamic status response for frontend dropdown"""
    def __init__(self, value: str, label: str, color: str = None, order: int = 0):
        self.value = value
        self.label = label
        self.color = color
        self.order = order


@router.get("/statuses/dynamic", summary="Dinamik statuslarni olish")
async def get_dynamic_statuses(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user)
):
    """
    Mijoz yaratish uchun barcha dinamik statuslarni olish
    Response: [{"value": "contacted", "label": "Contacted", "color": "#3B82F6", "order": 1}, ...]
    """
    # Get all active statuses from customer_status table
    result = await session.execute(
        select(customer_status_table)
        .where(customer_status_table.c.is_active == True)
        .order_by(customer_status_table.c.order)
    )
    statuses = result.fetchall()

    # Format for frontend dropdown
    return [
        {
            "value": s.name,
            "label": s.display_name,
            "color": s.color,
            "order": s.order,
            "description": s.description
        }
        for s in statuses
    ]
