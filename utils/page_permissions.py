from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin_models import app_page_table
from models.user_models import PageName, user_page_permission


DEFAULT_PAGE_DEFINITIONS = [
    {
        "name": PageName.ceo.value,
        "display_name": "Dashboard",
        "description": "CEO dashboard access",
        "route_path": "/ceo",
        "order": 1,
        "is_system": False,
    },
    {
        "name": PageName.payment_list.value,
        "display_name": "Payment",
        "description": "Payment list access",
        "route_path": "/payment_list",
        "order": 2,
        "is_system": False,
    },
    {
        "name": PageName.project_toggle.value,
        "display_name": "WordPress",
        "description": "WordPress controls access",
        "route_path": "/project_toggle",
        "order": 3,
        "is_system": False,
    },
    {
        "name": PageName.projects.value,
        "display_name": "Projects",
        "description": "Projects board access",
        "route_path": "/projects",
        "order": 4,
        "is_system": False,
    },
    {
        "name": PageName.crm.value,
        "display_name": "Sales CRM",
        "description": "CRM dashboard access",
        "route_path": "/crm",
        "order": 5,
        "is_system": False,
    },
    {
        "name": PageName.finance_list.value,
        "display_name": "Finance",
        "description": "Finance dashboard access",
        "route_path": "/finance_list",
        "order": 6,
        "is_system": False,
    },
    {
        "name": PageName.update_list.value,
        "display_name": "Update",
        "description": "Updates dashboard access",
        "route_path": "/update_list",
        "order": 7,
        "is_system": False,
    },
    {
        "name": PageName.company_payments.value,
        "display_name": "Company Payments",
        "description": "Company recurring payments access",
        "route_path": "/company-payments",
        "order": 8,
        "is_system": False,
    },
    {
        "name": "cognilabsai_chat",
        "display_name": "CognilabsAI Chat",
        "description": "CognilabsAI chat operations access",
        "route_path": "/cognilabsai/chat",
        "order": 90,
        "is_system": False,
    },
    {
        "name": "cognilabsai_integrations",
        "display_name": "CognilabsAI Integrations",
        "description": "CognilabsAI integrations access",
        "route_path": "/cognilabsai/integrations",
        "order": 91,
        "is_system": False,
    },
]


def normalize_page_name(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().lower()


async def ensure_app_page_schema(session: AsyncSession) -> None:
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS app_page (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) UNIQUE NOT NULL,
            display_name VARCHAR(255) NOT NULL,
            description TEXT,
            route_path VARCHAR(255),
            "order" INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            is_system BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_app_page_name ON app_page(name)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_app_page_active ON app_page(is_active)"))

    column_info = await session.execute(text("""
        SELECT data_type
        FROM information_schema.columns
        WHERE table_name = 'user_page_permission'
          AND column_name = 'page_name'
        LIMIT 1
    """))
    column_row = column_info.fetchone()
    if column_row and column_row.data_type != "character varying":
        await session.execute(text("""
            ALTER TABLE user_page_permission
            ALTER COLUMN page_name TYPE VARCHAR(100)
            USING page_name::text
        """))

    await session.commit()


async def initialize_default_pages(session: AsyncSession) -> None:
    await ensure_app_page_schema(session)

    result = await session.execute(select(func.count()).select_from(app_page_table))
    count = result.scalar() or 0
    if count > 0:
        return

    now = datetime.utcnow()
    for page in DEFAULT_PAGE_DEFINITIONS:
        await session.execute(
            insert(app_page_table).values(
                **page,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    await session.commit()


async def get_all_pages(session: AsyncSession, *, include_inactive: bool = True):
    await initialize_default_pages(session)

    query = select(app_page_table).order_by(app_page_table.c.order.asc(), app_page_table.c.id.asc())
    if not include_inactive:
        query = query.where(app_page_table.c.is_active == True)

    result = await session.execute(query)
    return result.fetchall()


async def get_page_display_map(session: AsyncSession, *, include_inactive: bool = True) -> dict[str, str]:
    pages = await get_all_pages(session, include_inactive=include_inactive)
    return {page.name: page.display_name for page in pages}


async def get_user_permission_names(session: AsyncSession, user_id: int) -> list[str]:
    result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == user_id)
        .order_by(user_page_permission.c.id.asc())
    )
    return [normalize_page_name(row.page_name) for row in result.fetchall()]


async def get_available_page_names(session: AsyncSession, *, include_inactive: bool = True) -> list[str]:
    pages = await get_all_pages(session, include_inactive=include_inactive)
    return [page.name for page in pages]


async def validate_page_names(
    session: AsyncSession,
    page_names: list[str],
    *,
    active_only: bool = False,
) -> tuple[list[str], list[str]]:
    pages = await get_all_pages(session, include_inactive=not active_only)
    valid_pages = {page.name for page in pages if include_page_for_validation(page, active_only=active_only)}

    normalized_names = [page_name.strip().lower() for page_name in page_names if page_name and page_name.strip()]
    invalid_names = [page_name for page_name in normalized_names if page_name not in valid_pages]
    return normalized_names, invalid_names


def include_page_for_validation(page: Any, *, active_only: bool) -> bool:
    if not active_only:
        return True
    return bool(page.is_active)


def build_permission_display_names(permission_names: list[str], page_display_map: dict[str, str]) -> list[str]:
    ordered_unique_names: list[str] = []
    seen: set[str] = set()
    for permission_name in permission_names:
        if permission_name in seen:
            continue
        seen.add(permission_name)
        ordered_unique_names.append(permission_name)
    return [page_display_map.get(permission_name, permission_name) for permission_name in ordered_unique_names]
