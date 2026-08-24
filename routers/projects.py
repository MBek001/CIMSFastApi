import asyncio
import re
from datetime import date, datetime, time
from typing import Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, UploadFile, File, Form, Query
from sqlalchemy import delete, exists, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update as TelegramUpdate
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from auth_utils.auth_func import get_current_active_user
from config import PROJECT_TASK_BOT_TOKEN, PROJECT_TASK_REPORT_CHAT_ID, PROJECT_TASK_REPORT_THREAD_ID
from database import async_session_maker, engine, get_async_session
from models.admin_models import daily_update_log
from models.projects_models import (
    CardPriority,
    project,
    project_attachment,
    project_team,
    project_team_member,
    project_member,
    project_board,
    project_board_column,
    project_board_card,
    project_board_card_assignee,
    project_board_card_file,
    project_board_card_status_history,
    ProjectAttachmentType,
)
from models.user_models import PageName, user, user_page_permission
from schemes.projects_schemes import (
    BoardCardFileResponse,
    BoardColumnResponse,
    BoardCreateRequest,
    BoardDetailResponse,
    BoardListItemResponse,
    BoardListResponse,
    BoardUpdateRequest,
    CardDetailResponse,
    CardListItemResponse,
    CardListResponse,
    CardMoveRequest,
    CardResponse,
    CardStatusHistoryResponse,
    ColumnCreateRequest,
    ColumnMoveRequest,
    ColumnUpdateRequest,
    ProjectAttachmentResponse,
    ProjectBoardsDetailResponse,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectSummaryResponse,
    ProjectTeamCreateRequest,
    ProjectTeamResponse,
    ProjectTeamUpdateRequest,
    ProjectTelegramGroupRequest,
    UserSummaryResponse,
)
from schemes.schemes_users import CreateResponse, SuccessResponse
from utils.file_storage import delete_file_if_exists, delete_image_if_exists, save_image, save_project_attachment_file
from utils.telegram_helper import send_card_assignment_notification

router = APIRouter(tags=["Projects"])

DEFAULT_BOARD_COLUMNS = [
    {"name": "To Do", "color": "#64748B"},
    {"name": "Doing", "color": "#0EA5E9"},
    {"name": "Done", "color": "#22C55E"},
    {"name": "To Test", "color": "#F59E0B"},
    {"name": "Refix", "color": "#EF4444"},
]

_project_card_schema_ready = False
_project_task_bot_app: Optional[Application] = None
PROJECTS_TZ = ZoneInfo("Asia/Tashkent")
TASK_COMMAND_RE = re.compile(r"/(?P<command>add_task_front|add_task_back|frontend|backend)(?:@\w+)?", re.IGNORECASE)
TASK_STATUS_ALIASES = {
    "todo": "To Do",
    "to_do": "To Do",
    "to-do": "To Do",
    "to do": "To Do",
    "doing": "Doing",
    "progress": "Doing",
    "inprogress": "Doing",
    "in_progress": "Doing",
    "done": "Done",
    "completed": "Done",
    "test": "To Test",
    "totest": "To Test",
    "to_test": "To Test",
    "to-test": "To Test",
    "to test": "To Test",
    "refix": "Refix",
    "fix": "Refix",
}


def normalize_project_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(PROJECTS_TZ).replace(tzinfo=None)


def is_ceo_user(current_user) -> bool:
    role = getattr(current_user, "role", None)
    role_name = getattr(role, "name", None)
    role_value = getattr(role, "value", None)
    company_code = str(getattr(current_user, "company_code", "") or "").strip().lower()

    role_name_normalized = str(role_name or "").strip().lower()
    role_value_normalized = str(role_value or "").strip().lower()
    role_plain_normalized = str(role or "").strip().lower()

    return (
        role_name_normalized == "ceo"
        or role_value_normalized == "ceo"
        or role_plain_normalized == "ceo"
        or company_code == "ceo"
    )


async def ensure_projects_page_access(session: AsyncSession, current_user) -> None:
    if current_user.company_code == "ceo":
        return

    result = await session.execute(
        select(user_page_permission.c.id).where(
            user_page_permission.c.user_id == current_user.id,
            user_page_permission.c.page_name == PageName.projects.value,
        )
    )
    if not result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Projects sahifasiga kirish ruxsatingiz yo'q",
        )


async def get_project_or_404(session: AsyncSession, project_id: int):
    result = await session.execute(select(project).where(project.c.id == project_id))
    project_row = result.fetchone()
    if not project_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project topilmadi")
    return project_row


async def ensure_project_card_schema() -> None:
    global _project_card_schema_ready
    if _project_card_schema_ready:
        return

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS project_team (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL UNIQUE,
                    description TEXT NULL,
                    created_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS project_team_member (
                    id SERIAL PRIMARY KEY,
                    team_id INTEGER NOT NULL REFERENCES project_team(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    CONSTRAINT uq_project_team_member UNIQUE (team_id, user_id)
                )
                """
            )
        )
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_team_member_team_id ON project_team_member(team_id)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_team_member_user_id ON project_team_member(user_id)"""))
        await conn.execute(
            text(
                """
                ALTER TABLE project
                ADD COLUMN IF NOT EXISTS team_id INTEGER NULL REFERENCES project_team(id) ON DELETE SET NULL
                """
            )
        )
        await conn.execute(text("""ALTER TABLE project ADD COLUMN IF NOT EXISTS deadline TIMESTAMP NULL"""))
        await conn.execute(text("""ALTER TABLE project ADD COLUMN IF NOT EXISTS telegram_group_id VARCHAR(100) NULL"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_team_id ON project(team_id)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_deadline ON project(deadline)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_telegram_group_id ON project(telegram_group_id)"""))
        await conn.execute(text("""ALTER TABLE project_board_card ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP NULL"""))
        await conn.execute(text("""ALTER TABLE project_board_card ADD COLUMN IF NOT EXISTS telegram_source_chat_id VARCHAR(100) NULL"""))
        await conn.execute(text("""ALTER TABLE project_board_card ADD COLUMN IF NOT EXISTS telegram_source_message_id VARCHAR(100) NULL"""))
        await conn.execute(text("""ALTER TABLE project_board_card ADD COLUMN IF NOT EXISTS telegram_source_command VARCHAR(50) NULL"""))
        await conn.execute(text("""ALTER TABLE project_board_card ADD COLUMN IF NOT EXISTS telegram_source_kind VARCHAR(20) NULL"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_board_card_due_date ON project_board_card(due_date)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_board_card_completed_at ON project_board_card(completed_at)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_board_card_telegram_source ON project_board_card(telegram_source_chat_id, telegram_source_message_id)"""))
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS project_board_card_status_history (
                    id SERIAL PRIMARY KEY,
                    card_id INTEGER NOT NULL REFERENCES project_board_card(id) ON DELETE CASCADE,
                    column_id INTEGER NULL REFERENCES project_board_column(id) ON DELETE SET NULL,
                    column_name VARCHAR(80) NOT NULL,
                    entered_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    left_at TIMESTAMP NULL,
                    duration_seconds INTEGER NULL,
                    moved_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL
                )
                """
            )
        )
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_card_status_history_card_id ON project_board_card_status_history(card_id)"""))
        await conn.execute(text("""CREATE INDEX IF NOT EXISTS idx_project_card_status_history_open ON project_board_card_status_history(card_id, left_at)"""))
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS project_board_card_assignee (
                    id SERIAL PRIMARY KEY,
                    card_id INTEGER NOT NULL REFERENCES project_board_card(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    CONSTRAINT uq_project_board_card_assignee UNIQUE (card_id, user_id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_project_board_card_assignee_card_id
                ON project_board_card_assignee(card_id)
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_project_board_card_assignee_user_id
                ON project_board_card_assignee(user_id)
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO project_board_card_assignee (card_id, user_id, created_at)
                SELECT card.id, card.assignee_id, COALESCE(card.updated_at, card.created_at, NOW())
                FROM project_board_card AS card
                WHERE card.assignee_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM project_board_card_assignee AS existing
                    WHERE existing.card_id = card.id
                      AND existing.user_id = card.assignee_id
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'project_board_card'
                          AND column_name = 'due_date'
                          AND data_type = 'date'
                    ) THEN
                        ALTER TABLE project_board_card
                        ALTER COLUMN due_date TYPE TIMESTAMP
                        USING CASE
                            WHEN due_date IS NULL THEN NULL
                            ELSE due_date::timestamp
                        END;
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO project_board_card_status_history (card_id, column_id, column_name, entered_at, moved_by)
                SELECT card.id, col.id, col.name, COALESCE(card.created_at, NOW()), card.created_by
                FROM project_board_card AS card
                JOIN project_board_column AS col ON col.id = card.column_id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM project_board_card_status_history AS history
                    WHERE history.card_id = card.id
                )
                """
            )
        )

    _project_card_schema_ready = True


async def is_project_member(session: AsyncSession, project_id: int, user_id: int) -> bool:
    membership = await session.execute(
        select(project_member.c.id).where(
            project_member.c.project_id == project_id,
            project_member.c.user_id == user_id,
        )
    )
    return membership.fetchone() is not None


async def ensure_project_member_access(session: AsyncSession, project_id: int, current_user):
    await ensure_projects_page_access(session, current_user)
    project_row = await get_project_or_404(session, project_id)
    if not await is_project_member(session, project_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Siz bu project a'zosi emassiz",
        )
    return project_row


async def get_board_or_404(session: AsyncSession, board_id: int, include_archived: bool = False):
    query = select(project_board).where(project_board.c.id == board_id)
    if not include_archived:
        query = query.where(project_board.c.is_archived == False)  # noqa: E712
    result = await session.execute(query)
    board_row = result.fetchone()
    if not board_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board topilmadi")
    return board_row


async def get_column_or_404(session: AsyncSession, column_id: int):
    result = await session.execute(
        select(project_board_column).where(project_board_column.c.id == column_id)
    )
    column_row = result.fetchone()
    if not column_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column topilmadi")
    return column_row


async def get_card_or_404(session: AsyncSession, card_id: int):
    result = await session.execute(select(project_board_card).where(project_board_card.c.id == card_id))
    card_row = result.fetchone()
    if not card_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card topilmadi")
    return card_row


async def get_user_map(session: AsyncSession, user_ids: Set[int]) -> Dict[int, UserSummaryResponse]:
    clean_ids = [user_id for user_id in user_ids if user_id is not None]
    if not clean_ids:
        return {}

    result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.email).where(user.c.id.in_(clean_ids))
    )
    return {
        row.id: UserSummaryResponse(id=row.id, name=row.name, surname=row.surname, email=row.email)
        for row in result.fetchall()
    }


async def ensure_valid_member_ids(
    session: AsyncSession,
    member_ids: Sequence[int],
    current_user_id: int,
) -> List[int]:
    normalized_ids = {member_id for member_id in member_ids if member_id is not None}
    normalized_ids.add(current_user_id)

    result = await session.execute(select(user.c.id).where(user.c.id.in_(list(normalized_ids))))
    existing_ids = {row.id for row in result.fetchall()}
    missing_ids = sorted(normalized_ids - existing_ids)
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Noto'g'ri member id lar: {missing_ids}",
        )

    return sorted(existing_ids)


def parse_member_ids_form(raw_member_ids: Optional[Sequence[str]]) -> Optional[List[int]]:
    if raw_member_ids is None:
        return None

    parsed_ids: List[int] = []
    for raw_value in raw_member_ids:
        if raw_value is None:
            continue
        for piece in str(raw_value).split(","):
            normalized_piece = piece.strip()
            if not normalized_piece:
                continue
            try:
                parsed_ids.append(int(normalized_piece))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"member_ids ichida noto'g'ri qiymat bor: '{normalized_piece}'",
                )
    return parsed_ids


def parse_assignee_ids_form(raw_assignee_ids: Optional[Sequence[str]]) -> Optional[List[int]]:
    if raw_assignee_ids is None:
        return None

    parsed_ids: List[int] = []
    for raw_value in raw_assignee_ids:
        if raw_value is None:
            continue
        for piece in str(raw_value).split(","):
            normalized_piece = piece.strip()
            if not normalized_piece:
                continue
            try:
                parsed_ids.append(int(normalized_piece))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"assignee_ids ichida noto'g'ri qiymat bor: '{normalized_piece}'",
                )
    return parsed_ids


async def ensure_user_exists(
    session: AsyncSession,
    user_id: Optional[int],
    detail_message: str = "Assignee user topilmadi",
) -> None:
    if user_id is None:
        return

    result = await session.execute(
        select(user.c.id).where(user.c.id == user_id)
    )
    if not result.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail_message,
        )


async def ensure_user_ids_exist(
    session: AsyncSession,
    user_ids: Sequence[int],
    detail_message: str = "Assignee user topilmadi",
) -> List[int]:
    normalized_ids: List[int] = []
    seen_ids: Set[int] = set()
    for user_id in user_ids:
        if user_id is None or user_id in seen_ids:
            continue
        seen_ids.add(user_id)
        normalized_ids.append(user_id)

    if not normalized_ids:
        return []

    result = await session.execute(
        select(user.c.id).where(user.c.id.in_(normalized_ids))
    )
    existing_ids = {row.id for row in result.fetchall()}
    missing_ids = [user_id for user_id in normalized_ids if user_id not in existing_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{detail_message}: {missing_ids}",
        )

    return normalized_ids


def resolve_assignee_input(
    assignee_id: Optional[int],
    assignee_ids: Optional[Sequence[int]],
) -> Optional[List[int]]:
    if assignee_ids is None:
        if assignee_id is None:
            return None
        return [assignee_id]

    resolved_ids: List[int] = []
    seen_ids: Set[int] = set()
    if assignee_id is not None:
        seen_ids.add(assignee_id)
        resolved_ids.append(assignee_id)
    for target_id in assignee_ids:
        if target_id is None or target_id in seen_ids:
            continue
        seen_ids.add(target_id)
        resolved_ids.append(target_id)
    return resolved_ids


async def get_card_assignee_ids_map(
    session: AsyncSession,
    card_ids: Sequence[int],
) -> Dict[int, List[int]]:
    if not card_ids:
        return {}

    result = await session.execute(
        select(
            project_board_card_assignee.c.card_id,
            project_board_card_assignee.c.user_id,
        )
        .where(project_board_card_assignee.c.card_id.in_(list(card_ids)))
        .order_by(
            project_board_card_assignee.c.card_id.asc(),
            project_board_card_assignee.c.created_at.asc(),
            project_board_card_assignee.c.id.asc(),
        )
    )

    assignee_ids_map: Dict[int, List[int]] = {}
    for row in result.fetchall():
        assignee_ids_map.setdefault(row.card_id, []).append(row.user_id)
    return assignee_ids_map


def get_card_assignee_ids(card_row, assignee_ids_map: Dict[int, List[int]]) -> List[int]:
    assignee_ids = list(assignee_ids_map.get(card_row.id, []))
    if card_row.assignee_id is not None and card_row.assignee_id not in assignee_ids:
        assignee_ids.insert(0, card_row.assignee_id)
    return assignee_ids


async def sync_card_assignees(
    session: AsyncSession,
    card_id: int,
    assignee_ids: Sequence[int],
) -> List[int]:
    normalized_ids: List[int] = []
    seen_ids: Set[int] = set()
    for assignee_id in assignee_ids:
        if assignee_id is None or assignee_id in seen_ids:
            continue
        seen_ids.add(assignee_id)
        normalized_ids.append(assignee_id)

    await session.execute(
        delete(project_board_card_assignee).where(project_board_card_assignee.c.card_id == card_id)
    )
    if normalized_ids:
        await session.execute(
            insert(project_board_card_assignee).values(
                [
                    {
                        "card_id": card_id,
                        "user_id": assignee_id,
                        "created_at": datetime.utcnow(),
                    }
                    for assignee_id in normalized_ids
                ]
            )
        )

    await session.execute(
        update(project_board_card)
        .where(project_board_card.c.id == card_id)
        .values(assignee_id=normalized_ids[0] if normalized_ids else None)
    )
    return normalized_ids


def build_card_user_filter(target_user_id: int):
    assigned_to_user = exists(
        select(project_board_card_assignee.c.id).where(
            project_board_card_assignee.c.card_id == project_board_card.c.id,
            project_board_card_assignee.c.user_id == target_user_id,
        )
    )
    has_any_assignee = exists(
        select(project_board_card_assignee.c.id).where(
            project_board_card_assignee.c.card_id == project_board_card.c.id,
        )
    )

    return assigned_to_user | (
        ~has_any_assignee
        & (
            (project_board_card.c.assignee_id == target_user_id)
            | (project_board_card.c.assignee_id.is_(None) & (project_board_card.c.created_by == target_user_id))
        )
    )


async def ensure_card_access(session: AsyncSession, card_row, current_user):
    await ensure_projects_page_access(session, current_user)
    column_row = await get_column_or_404(session, card_row.column_id)
    board_row = await get_board_or_404(session, column_row.board_id, include_archived=True)
    return column_row, board_row


async def ensure_project_visible_for_user(session: AsyncSession, project_id: int, user_id: int):
    project_row = await get_project_or_404(session, project_id)

    if await is_project_member(session, project_id, user_id):
        return project_row

    visible_card = await session.execute(
        select(project_board_card.c.id)
        .select_from(
            project_board_card
            .join(project_board_column, project_board_card.c.column_id == project_board_column.c.id)
            .join(project_board, project_board_column.c.board_id == project_board.c.id)
        )
        .where(
            project_board.c.project_id == project_id,
            build_card_user_filter(user_id),
        )
        .limit(1)
    )
    if visible_card.fetchone():
        return project_row

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Bu userga tegishli project topilmadi",
    )


async def resequence_columns(session: AsyncSession, column_ids: Sequence[int]) -> None:
    for index, column_id in enumerate(column_ids):
        await session.execute(
            update(project_board_column)
            .where(project_board_column.c.id == column_id)
            .values(order=-(index + 1))
        )

    for index, column_id in enumerate(column_ids):
        await session.execute(
            update(project_board_column)
            .where(project_board_column.c.id == column_id)
            .values(order=index)
        )


async def resequence_cards(session: AsyncSession, card_ids: Sequence[int]) -> None:
    for index, card_id in enumerate(card_ids):
        await session.execute(
            update(project_board_card)
            .where(project_board_card.c.id == card_id)
            .values(order=-(index + 1))
        )

    for index, card_id in enumerate(card_ids):
        await session.execute(
            update(project_board_card)
            .where(project_board_card.c.id == card_id)
            .values(order=index)
        )


def clamp_position(position: int, max_length: int) -> int:
    return max(0, min(position, max_length))


async def get_project_counts(
    session: AsyncSession, project_ids: Sequence[int]
) -> Tuple[Dict[int, int], Dict[int, int]]:
    if not project_ids:
        return {}, {}

    member_counts_result = await session.execute(
        select(project_member.c.project_id, func.count(project_member.c.id).label("count"))
        .where(project_member.c.project_id.in_(list(project_ids)))
        .group_by(project_member.c.project_id)
    )
    board_counts_result = await session.execute(
        select(project_board.c.project_id, func.count(project_board.c.id).label("count"))
        .where(
            project_board.c.project_id.in_(list(project_ids)),
            project_board.c.is_archived == False,  # noqa: E712
        )
        .group_by(project_board.c.project_id)
    )

    member_counts = {row.project_id: row.count for row in member_counts_result.fetchall()}
    board_counts = {row.project_id: row.count for row in board_counts_result.fetchall()}
    return member_counts, board_counts


async def get_card_files_map(
    session: AsyncSession, card_ids: Sequence[int]
) -> Dict[int, List[BoardCardFileResponse]]:
    if not card_ids:
        return {}

    result = await session.execute(
        select(project_board_card_file)
        .where(project_board_card_file.c.card_id.in_(list(card_ids)))
        .order_by(project_board_card_file.c.created_at.asc())
    )

    files_map: Dict[int, List[BoardCardFileResponse]] = {}
    for row in result.fetchall():
        files_map.setdefault(row.card_id, []).append(
            BoardCardFileResponse(
                id=row.id,
                card_id=row.card_id,
                created_at=row.created_at,
                url_path=row.url_path,
            )
        )
    return files_map


async def save_card_images(
    session: AsyncSession,
    card_id: int,
    images: Optional[List[UploadFile]],
) -> None:
    if not images:
        return

    image_rows = []
    for image in images:
        if not image or not image.filename:
            continue
        image_path = await save_image(image, "card")
        image_rows.append(
            {
                "card_id": card_id,
                "created_at": datetime.utcnow(),
                "url_path": image_path,
            }
        )

    if image_rows:
        await session.execute(insert(project_board_card_file).values(image_rows))


def delete_card_file_paths(file_rows: Sequence) -> None:
    for file_row in file_rows:
        delete_image_if_exists(file_row.url_path)


async def ensure_team_exists(session: AsyncSession, team_id: Optional[int]) -> None:
    if team_id is None:
        return
    result = await session.execute(select(project_team.c.id).where(project_team.c.id == team_id))
    if result.scalar() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team topilmadi")


async def get_team_member_ids(session: AsyncSession, team_id: Optional[int]) -> List[int]:
    if team_id is None:
        return []
    result = await session.execute(
        select(project_team_member.c.user_id)
        .where(project_team_member.c.team_id == team_id)
        .order_by(project_team_member.c.user_id.asc())
    )
    return [row.user_id for row in result.fetchall()]


async def get_project_team_response(session: AsyncSession, team_id: Optional[int]) -> Optional[ProjectTeamResponse]:
    if team_id is None:
        return None
    team_result = await session.execute(select(project_team).where(project_team.c.id == team_id))
    team_row = team_result.fetchone()
    if not team_row:
        return None
    members_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.email)
        .join(project_team_member, project_team_member.c.user_id == user.c.id)
        .where(project_team_member.c.team_id == team_id)
        .order_by(user.c.name.asc(), user.c.surname.asc(), user.c.id.asc())
    )
    return ProjectTeamResponse(
        id=team_row.id,
        name=team_row.name,
        description=team_row.description,
        created_by=team_row.created_by,
        created_at=team_row.created_at,
        updated_at=team_row.updated_at,
        members=[
            UserSummaryResponse(id=row.id, name=row.name, surname=row.surname, email=row.email)
            for row in members_result.fetchall()
        ],
    )


async def sync_team_members(session: AsyncSession, team_id: int, member_ids: Sequence[int]) -> None:
    validated_ids = await ensure_user_ids_exist(session, member_ids, "Team user topilmadi")
    await session.execute(delete(project_team_member).where(project_team_member.c.team_id == team_id))
    if validated_ids:
        await session.execute(
            insert(project_team_member).values(
                [
                    {"team_id": team_id, "user_id": member_id, "created_at": datetime.utcnow()}
                    for member_id in validated_ids
                ]
            )
        )


def is_done_column_name(name: str) -> bool:
    return str(name or "").strip().lower() in {"done", "completed", "finish", "finished", "yakunlandi", "bajarildi"}


async def open_card_status_history(
    session: AsyncSession,
    card_id: int,
    column_id: int,
    column_name: str,
    moved_by: Optional[int],
) -> None:
    await session.execute(
        insert(project_board_card_status_history).values(
            card_id=card_id,
            column_id=column_id,
            column_name=column_name,
            entered_at=datetime.utcnow(),
            moved_by=moved_by,
        )
    )


async def close_open_card_status_history(session: AsyncSession, card_id: int) -> None:
    now = datetime.utcnow()
    rows = (
        await session.execute(
            select(project_board_card_status_history)
            .where(
                project_board_card_status_history.c.card_id == card_id,
                project_board_card_status_history.c.left_at.is_(None),
            )
            .order_by(project_board_card_status_history.c.entered_at.desc(), project_board_card_status_history.c.id.desc())
        )
    ).fetchall()
    for row in rows:
        seconds = max(int((now - row.entered_at).total_seconds()), 0) if row.entered_at else 0
        await session.execute(
            update(project_board_card_status_history)
            .where(project_board_card_status_history.c.id == row.id)
            .values(left_at=now, duration_seconds=seconds)
        )


async def get_card_status_history_payload(session: AsyncSession, card_id: int) -> List[CardStatusHistoryResponse]:
    rows = (
        await session.execute(
            select(project_board_card_status_history)
            .where(project_board_card_status_history.c.card_id == card_id)
            .order_by(project_board_card_status_history.c.entered_at.asc(), project_board_card_status_history.c.id.asc())
        )
    ).fetchall()
    now = datetime.utcnow()
    payload = []
    for row in rows:
        duration_seconds = row.duration_seconds
        if row.left_at is None and row.entered_at:
            duration_seconds = max(int((now - row.entered_at).total_seconds()), 0)
        payload.append(
            CardStatusHistoryResponse(
                id=row.id,
                card_id=row.card_id,
                column_id=row.column_id,
                column_name=row.column_name,
                entered_at=row.entered_at,
                left_at=row.left_at,
                duration_seconds=duration_seconds,
                moved_by=row.moved_by,
            )
        )
    return payload


async def get_current_status_duration_seconds(session: AsyncSession, card_id: int) -> Optional[int]:
    row = (
        await session.execute(
            select(project_board_card_status_history.c.entered_at)
            .where(
                project_board_card_status_history.c.card_id == card_id,
                project_board_card_status_history.c.left_at.is_(None),
            )
            .order_by(project_board_card_status_history.c.entered_at.desc(), project_board_card_status_history.c.id.desc())
            .limit(1)
        )
    ).fetchone()
    if not row or not row.entered_at:
        return None
    return max(int((datetime.utcnow() - row.entered_at).total_seconds()), 0)


def completion_duration_seconds(card_row) -> Optional[int]:
    if not getattr(card_row, "completed_at", None) or not getattr(card_row, "created_at", None):
        return None
    return max(int((card_row.completed_at - card_row.created_at).total_seconds()), 0)


async def write_task_move_daily_update(
    session: AsyncSession,
    actor_user_id: int,
    card_title: str,
    source_name: str,
    target_name: str,
) -> None:
    today = date.today()
    line = f"- Task move: {card_title} ({source_name} -> {target_name})"
    marker = f"system-task-move:{actor_user_id}:{today.isoformat()}"
    existing = (
        await session.execute(
            select(daily_update_log)
            .where(
                daily_update_log.c.user_id == actor_user_id,
                daily_update_log.c.update_date == today,
                daily_update_log.c.telegram_message_id == marker,
            )
            .order_by(daily_update_log.c.id.desc())
            .limit(1)
        )
    ).fetchone()
    if existing:
        content = f"{existing.update_content}\n{line}" if existing.update_content else line
        await session.execute(
            update(daily_update_log)
            .where(daily_update_log.c.id == existing.id)
            .values(update_content=content, parsed_at=datetime.utcnow())
        )
        return
    actor = (
        await session.execute(select(user.c.telegram_id).where(user.c.id == actor_user_id))
    ).fetchone()
    await session.execute(
        insert(daily_update_log).values(
            user_id=actor_user_id,
            telegram_username=actor.telegram_id if actor else None,
            update_date=today,
            update_content=line,
            telegram_message_id=marker,
            is_valid=True,
            parsed_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
    )


def parse_project_task_deadline(text_value: str) -> Tuple[str, Optional[datetime]]:
    raw_text = text_value or ""
    due_match = re.search(
        r"(?:deadline|due|muddat):\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)(?:[ T]+(\d{1,2}:\d{2}))?",
        raw_text,
        re.IGNORECASE,
    )
    if not due_match:
        due_match = re.search(
            r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)(?:[ T]+(\d{1,2}:\d{2}))?\b",
            raw_text,
            re.IGNORECASE,
        )
    if not due_match:
        return re.sub(r"\s+", " ", raw_text).strip(" -:\n\t"), None

    raw_date = due_match.group(1)
    raw_time = due_match.group(2) or "18:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
        due_date = datetime.strptime(f"{raw_date} {raw_time}", "%Y-%m-%d %H:%M")
    else:
        parts = re.split(r"[./-]", raw_date)
        day = int(parts[0])
        month = int(parts[1])
        if len(parts) >= 3:
            year = int(parts[2])
            year = 2000 + year if year < 100 else year
        else:
            today = datetime.now(PROJECTS_TZ).date()
            year = today.year
            if date(year, month, day) < today:
                year += 1
        hour, minute = [int(piece) for piece in raw_time.split(":", 1)]
        due_date = datetime.combine(date(year, month, day), time(hour, minute))

    cleaned = raw_text[: due_match.start()] + raw_text[due_match.end() :]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:\n\t")
    return cleaned, due_date


def normalize_task_status(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw_value = re.sub(r"\s+", " ", value).strip(" -:_\n\t")
    if not raw_value:
        return None
    key = raw_value.lower().replace("_", " ").replace("-", " ")
    compact_key = key.replace(" ", "")
    return TASK_STATUS_ALIASES.get(key) or TASK_STATUS_ALIASES.get(compact_key) or raw_value


def parse_project_task_status(text_value: str) -> Tuple[str, Optional[str]]:
    match = re.search(
        r"(?:status|column|holat|ustun):\s*([A-Za-zА-Яа-я0-9 _-]+?)(?=$|\s+(?:deadline|due|muddat):|\s+\d{4}-\d{2}-\d{2}|\s+\d{1,2}[./-]\d{1,2})",
        text_value,
        re.IGNORECASE,
    )
    if not match:
        return text_value, None
    status_text = normalize_task_status(match.group(1))
    cleaned = text_value[: match.start()] + text_value[match.end():]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:\n\t")
    return cleaned, status_text


def parse_project_task_command(text_value: str, reply_text: Optional[str] = None) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[datetime], Optional[str], Optional[str]]:
    text_value = text_value.strip()
    command_match = TASK_COMMAND_RE.search(text_value)
    if not command_match:
        return None, None, None, None, None, None

    command = command_match.group("command").lower()
    kind = "frontend" if command in {"add_task_front", "frontend"} else "backend"
    body = f"{text_value[:command_match.start()]} {text_value[command_match.end():]}"
    body, status_name = parse_project_task_status(body)
    body, due_date = parse_project_task_deadline(body)
    reply_body = re.sub(r"\s+", " ", str(reply_text or "")).strip()

    if reply_body:
        title = reply_body[:200]
        description = body or f"Telegram groupdan qo'shildi: {kind}"
    else:
        title = body[:200]
        description = f"Telegram groupdan qo'shildi: {kind}"

    return kind, title or None, description, due_date, command, status_name


async def find_user_by_telegram_username(session: AsyncSession, username: Optional[str]):
    if not username:
        return None
    normalized = username.strip().lower().lstrip("@")
    return (
        await session.execute(
            select(user)
            .where(func.lower(func.replace(user.c.telegram_id, "@", "")) == normalized)
            .limit(1)
        )
    ).fetchone()


def normalize_task_report_lookup(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


async def find_user_for_task_report(session: AsyncSession, lookup: str):
    normalized = normalize_task_report_lookup(lookup)
    if not normalized:
        return None

    rows = (
        await session.execute(
            select(
                user.c.id,
                user.c.name,
                user.c.surname,
                user.c.email,
                user.c.telegram_id,
                user.c.chat_id,
                user.c.job_title,
                user.c.role_name,
            )
            .order_by(user.c.is_active.desc(), user.c.id.asc())
        )
    ).fetchall()

    for row in rows:
        candidates = [
            row.telegram_id,
            str(row.telegram_id or "").lstrip("@"),
            row.email,
            str(row.email or "").split("@", 1)[0],
            row.name,
            row.surname,
            f"{row.name}{row.surname}",
            f"{row.surname}{row.name}",
        ]
        if any(normalize_task_report_lookup(candidate) == normalized for candidate in candidates):
            return row

    for row in rows:
        candidates = [row.telegram_id, row.email, row.name, row.surname, f"{row.name}{row.surname}"]
        if any(normalized in normalize_task_report_lookup(candidate) for candidate in candidates):
            return row

    return None


async def register_project_task_chat(session: AsyncSession, username: Optional[str], chat_id: int, email: Optional[str] = None) -> bool:
    target = await find_user_by_telegram_username(session, username)
    if not target and email:
        target = (
            await session.execute(
                select(user)
                .where(func.lower(user.c.email) == email.strip().lower())
                .limit(1)
            )
        ).fetchone()
    if not target:
        return False
    await session.execute(
        update(user)
        .where(user.c.id == target.id)
        .values(chat_id=str(chat_id))
    )
    await session.commit()
    return True


def user_matches_task_kind(row, kind: str) -> bool:
    haystack = " ".join(
        str(value or "").lower()
        for value in [
            getattr(row, "job_title", None),
            getattr(row, "role_name", None),
            getattr(row, "company_code", None),
            getattr(row, "email", None),
        ]
    )
    aliases = {
        "backend": ["backend", "back-end", "python", "fastapi", "api"],
        "frontend": ["frontend", "front-end", "react", "vue", "next", "web"],
    }
    return any(alias in haystack for alias in aliases.get(kind, [kind]))


async def resolve_project_task_assignees(session: AsyncSession, project_row, kind: str) -> List:
    source = project_team_member if project_row.team_id else project_member
    project_filter = source.c.team_id == project_row.team_id if project_row.team_id else source.c.project_id == project_row.id
    rows = (
        await session.execute(
            select(
                user.c.id,
                user.c.name,
                user.c.surname,
                user.c.email,
                user.c.telegram_id,
                user.c.chat_id,
                user.c.job_title,
                user.c.role_name,
                user.c.company_code,
            )
            .join(source, source.c.user_id == user.c.id)
            .where(project_filter)
            .order_by(user.c.id.asc())
        )
    ).fetchall()
    if len(rows) == 1:
        return list(rows)
    return [row for row in rows if user_matches_task_kind(row, kind)]


async def get_or_create_project_board_by_name(
    session: AsyncSession,
    project_id: int,
    board_name: str,
    created_by: Optional[int],
):
    board_row = (
        await session.execute(
            select(project_board)
            .where(
                project_board.c.project_id == project_id,
                func.lower(project_board.c.name) == board_name.lower(),
                project_board.c.is_archived == False,
            )
            .limit(1)
        )
    ).fetchone()
    if board_row:
        return board_row
    result = await session.execute(
        insert(project_board)
        .values(
            project_id=project_id,
            name=board_name,
            description=None,
            created_by=created_by,
            created_at=datetime.utcnow(),
            is_archived=False,
        )
        .returning(project_board)
    )
    board_row = result.fetchone()
    await session.execute(
        insert(project_board_column).values(
            [
                {
                    "board_id": board_row.id,
                    "name": column_data["name"],
                    "order": index,
                    "color": column_data["color"],
                    "created_at": datetime.utcnow(),
                }
                for index, column_data in enumerate(DEFAULT_BOARD_COLUMNS)
            ]
        )
    )
    return board_row


async def get_first_board_column(session: AsyncSession, board_id: int):
    column_row = (
        await session.execute(
            select(project_board_column)
            .where(project_board_column.c.board_id == board_id)
            .order_by(project_board_column.c.order.asc(), project_board_column.c.id.asc())
            .limit(1)
        )
    ).fetchone()
    if column_row:
        return column_row
    result = await session.execute(
        insert(project_board_column)
        .values(
            board_id=board_id,
            name=DEFAULT_BOARD_COLUMNS[0]["name"],
            order=0,
            color=DEFAULT_BOARD_COLUMNS[0]["color"],
            created_at=datetime.utcnow(),
        )
        .returning(project_board_column)
    )
    return result.fetchone()


async def resolve_board_column_for_task(session: AsyncSession, board_id: int, status_name: Optional[str]):
    if not status_name:
        return await get_first_board_column(session, board_id), None

    normalized_status = normalize_task_status(status_name)
    column_row = (
        await session.execute(
            select(project_board_column)
            .where(
                project_board_column.c.board_id == board_id,
                func.lower(project_board_column.c.name) == str(normalized_status).lower(),
            )
            .order_by(project_board_column.c.order.asc(), project_board_column.c.id.asc())
            .limit(1)
        )
    ).fetchone()
    if column_row:
        return column_row, None

    fallback_column = await get_first_board_column(session, board_id)
    return fallback_column, f"Status topilmadi: {status_name}. Task {fallback_column.name} ga qo'yildi."


async def create_project_task_from_group_message(
    chat_id: int,
    sender_username: Optional[str],
    text_value: str,
    source_message_id: int,
    reply_text: Optional[str] = None,
    reply_message_id: Optional[int] = None,
) -> dict:
    await ensure_project_card_schema()
    try:
        kind, task_title, task_description, due_date, command, status_name = parse_project_task_command(text_value, reply_text)
    except ValueError:
        return {"status": "error", "message": "Sana formati noto'g'ri. Masalan: /add_task_front 25.08 yoki /add_task_front 25.08 19:00"}
    duplicate_message_id = reply_message_id or source_message_id
    if not kind:
        return {"status": "ignored"}
    if not task_title:
        return {"status": "error", "message": "Task text topilmadi"}
    async with async_session_maker() as session:
        project_row = (
            await session.execute(
                select(project)
                .where(project.c.telegram_group_id == str(chat_id))
                .order_by(project.c.id.desc())
                .limit(1)
            )
        ).fetchone()
        if not project_row:
            return {"status": "error", "message": "Bu Telegram group projectga bog'lanmagan"}
        duplicate_row = (
            await session.execute(
                select(project_board_card.c.id)
                .where(
                    project_board_card.c.telegram_source_chat_id == str(chat_id),
                    project_board_card.c.telegram_source_message_id == str(duplicate_message_id),
                    project_board_card.c.telegram_source_kind == kind,
                )
                .limit(1)
            )
        ).fetchone()
        if duplicate_row:
            return {"status": "duplicate", "card_id": duplicate_row.id}
        sender_row = await find_user_by_telegram_username(session, sender_username)
        assignee_rows = await resolve_project_task_assignees(session, project_row, kind)
        assignee_ids = [row.id for row in assignee_rows]
        board_row = await get_or_create_project_board_by_name(
            session,
            project_row.id,
            "Backend" if kind == "backend" else "Frontend",
            sender_row.id if sender_row else None,
        )
        column_row, status_warning = await resolve_board_column_for_task(session, board_row.id, status_name)
        count_result = await session.execute(
            select(func.count(project_board_card.c.id)).where(project_board_card.c.column_id == column_row.id)
        )
        next_order = int(count_result.scalar() or 0)
        result = await session.execute(
            insert(project_board_card)
            .values(
                column_id=column_row.id,
                title=task_title,
                description=task_description,
                order=next_order,
                priority=CardPriority.medium,
                assignee_id=assignee_ids[0] if assignee_ids else None,
                due_date=normalize_project_datetime(due_date),
                telegram_source_chat_id=str(chat_id),
                telegram_source_message_id=str(duplicate_message_id),
                telegram_source_command=command,
                telegram_source_kind=kind,
                created_by=sender_row.id if sender_row else None,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            .returning(project_board_card.c.id)
        )
        card_id = result.scalar_one()
        await sync_card_assignees(session, card_id, assignee_ids)
        await open_card_status_history(session, card_id, column_row.id, column_row.name, sender_row.id if sender_row else None)
        await session.commit()
        for assignee_row in assignee_rows:
            if not assignee_row.chat_id:
                continue
            await send_card_assignment_notification(
                assignee_row.chat_id,
                task_title,
                task_description,
                CardPriority.medium.value,
                due_date,
                sender_username or "Telegram group",
                project_row.project_name,
                column_row.name,
            )
        return {
            "status": "ok",
            "card_id": card_id,
            "project_id": project_row.id,
            "board": board_row.name,
            "column": column_row.name,
            "assignee_ids": assignee_ids,
            "due_date": due_date,
            "warning": status_warning,
        }


def format_project_task_date(value: Optional[datetime]) -> str:
    if not value:
        return "-"
    return value.strftime("%d.%m.%Y %H:%M")


def task_priority_value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "-")


def task_status_emoji(status_name: Optional[str]) -> str:
    normalized = str(status_name or "").strip().lower()
    if normalized == "to do":
        return "⚪"
    if normalized == "doing":
        return "🔵"
    if normalized == "done":
        return "🟢"
    if normalized == "to test":
        return "🟡"
    if normalized == "refix":
        return "🔴"
    return "📍"


async def build_user_task_report(session: AsyncSession, lookup: str) -> Tuple[Optional[str], Optional[str]]:
    target_user = await find_user_for_task_report(session, lookup)
    if not target_user:
        return None, "User topilmadi"

    rows = (
        await session.execute(
            select(
                project_board_card,
                project_board_column.c.name.label("column_name"),
                project_board.c.name.label("board_name"),
                project.c.project_name.label("project_name"),
                project.c.id.label("project_id"),
            )
            .select_from(
                project_board_card
                .join(project_board_column, project_board_card.c.column_id == project_board_column.c.id)
                .join(project_board, project_board_column.c.board_id == project_board.c.id)
                .join(project, project_board.c.project_id == project.c.id)
            )
            .where(build_card_user_filter(target_user.id))
            .where(func.lower(project_board_column.c.name).in_(["to do", "doing"]))
            .order_by(project.c.project_name.asc(), project_board_card.c.completed_at.is_(None).desc(), project_board_card.c.due_date.asc().nullslast(), project_board_card.c.updated_at.desc())
        )
    ).fetchall()

    full_name = f"{target_user.name} {target_user.surname}".strip()
    role_text = target_user.job_title or target_user.role_name or "-"
    lines = [
        f"👤 {full_name}",
        f"🏷 {role_text}",
        f"📌 To Do / Doing: {len(rows)}",
        "",
    ]

    if not rows:
        lines.append("Aktiv yoki eski task topilmadi.")
        return "\n".join(lines), None

    now = datetime.utcnow()
    grouped: Dict[str, List] = {}
    for row in rows:
        grouped.setdefault(row.project_name or "Projectsiz", []).append(row)

    for project_name, project_rows in grouped.items():
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"🗂 {project_name}")
        lines.append(f"Tasklar: {len(project_rows)}")
        lines.append("")
        for index, row in enumerate(project_rows, start=1):
            status_text = row.column_name or "-"
            deadline_text = format_project_task_date(row.due_date) if row.due_date else "belgilanmagan"
            overdue_text = ""
            if row.due_date and not row.completed_at and not is_done_column_name(status_text) and row.due_date < now:
                overdue_text = " · ⚠️ kechikkan"
            lines.append(f"{index}. #{row.id} · {task_status_emoji(status_text)} {status_text}")
            lines.append(f"{row.title}")
            lines.append(f"Board: {row.board_name}")
            lines.append(f"Muddat: {deadline_text}{overdue_text} · Priority: {task_priority_value(row.priority)}")
            lines.append("")
        lines.append("")

    return "\n".join(lines).strip(), None


def is_project_task_report_target(update_obj: TelegramUpdate) -> bool:
    chat = update_obj.effective_chat
    message = update_obj.effective_message
    if not chat or not message:
        return False
    if PROJECT_TASK_REPORT_CHAT_ID and str(chat.id) != str(PROJECT_TASK_REPORT_CHAT_ID):
        return False
    if PROJECT_TASK_REPORT_THREAD_ID:
        return str(getattr(message, "message_thread_id", "") or "") == str(PROJECT_TASK_REPORT_THREAD_ID)
    return True


def split_telegram_text(text_value: str, limit: int = 3900) -> List[str]:
    chunks = []
    current = ""
    for line in text_value.splitlines():
        next_value = f"{current}\n{line}" if current else line
        if len(next_value) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = next_value
    if current:
        chunks.append(current)
    return chunks or [text_value[:limit]]


async def project_task_report_handler(update_obj: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update_obj.effective_message
    if not message or not message.text or not is_project_task_report_target(update_obj):
        return

    command = message.text.split(maxsplit=1)[0].split("@", 1)[0].lstrip("/")
    if command.lower() in {"start", "add_task_front", "add_task_back", "backend", "frontend"}:
        return
    async with async_session_maker() as session:
        report, error = await build_user_task_report(session, command)
    if error:
        await message.reply_text(error)
        return
    for chunk in split_telegram_text(report or ""):
        await message.reply_text(chunk)


async def project_task_start_handler(update_obj: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update_obj.effective_message
    if not message or not update_obj.effective_chat or not update_obj.effective_user:
        return
    if update_obj.effective_chat.type not in {"private"}:
        await message.reply_text("Botga private chatda /start yuboring.")
        return
    async with async_session_maker() as session:
        registered = await register_project_task_chat(
            session,
            update_obj.effective_user.username,
            update_obj.effective_chat.id,
            context.args[0] if context.args else None,
        )
    if registered:
        await message.reply_text("Task bot ulandi. Endi tasklar shu chatga keladi.")
    else:
        await message.reply_text("User topilmadi. CIMS profile telegram_id username bilan bir xil bo'lishi kerak yoki /start email@example.com yuboring.")


async def project_task_command_handler(update_obj: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update_obj.effective_message
    if not message or not message.text or not update_obj.effective_chat:
        return
    if update_obj.effective_chat.type not in {"group", "supergroup"}:
        return
    reply_text = None
    reply_message_id = None
    if message.reply_to_message:
        reply_text = message.reply_to_message.text or message.reply_to_message.caption
        reply_message_id = message.reply_to_message.message_id
    result = await create_project_task_from_group_message(
        update_obj.effective_chat.id,
        update_obj.effective_user.username if update_obj.effective_user else None,
        message.text,
        message.message_id,
        reply_text,
        reply_message_id,
    )
    if result.get("status") == "ignored":
        return
    if result.get("status") == "duplicate":
        await message.reply_text(f"Bu message oldin task qilingan: #{result['card_id']}")
        return
    if result.get("status") != "ok":
        await message.reply_text(result.get("message", "Task yaratilmadi"))
        return
    assignee_count = len(result.get("assignee_ids") or [])
    assignee_text = f" assignees={assignee_count}" if assignee_count else " assignee topilmadi"
    warning_text = f"\n{result['warning']}" if result.get("warning") else ""
    await message.reply_text(f"Task yaratildi: #{result['card_id']} ({result['board']} / {result['column']}){assignee_text}{warning_text}")


async def project_task_text_handler(update_obj: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> None:
    await project_task_command_handler(update_obj, context)


async def start_project_task_bot() -> None:
    global _project_task_bot_app
    token = PROJECT_TASK_BOT_TOKEN
    if _project_task_bot_app or not token:
        return
    _project_task_bot_app = Application.builder().token(token).build()
    _project_task_bot_app.add_handler(CommandHandler("start", project_task_start_handler))
    _project_task_bot_app.add_handler(CommandHandler("add_task_front", project_task_command_handler))
    _project_task_bot_app.add_handler(CommandHandler("add_task_back", project_task_command_handler))
    _project_task_bot_app.add_handler(CommandHandler("backend", project_task_command_handler))
    _project_task_bot_app.add_handler(CommandHandler("frontend", project_task_command_handler))
    _project_task_bot_app.add_handler(MessageHandler(filters.TEXT, project_task_text_handler))
    _project_task_bot_app.add_handler(MessageHandler(filters.COMMAND, project_task_report_handler), group=1)
    await _project_task_bot_app.initialize()
    await _project_task_bot_app.start()
    await _project_task_bot_app.updater.start_polling(drop_pending_updates=True)
    print("[projects] Project task bot ishga tushdi")


async def shutdown_project_task_bot() -> None:
    global _project_task_bot_app
    if not _project_task_bot_app:
        return
    await _project_task_bot_app.updater.stop()
    await _project_task_bot_app.stop()
    await _project_task_bot_app.shutdown()
    _project_task_bot_app = None


async def get_project_attachments_map(
    session: AsyncSession, project_ids: Sequence[int]
) -> Dict[int, List[ProjectAttachmentResponse]]:
    if not project_ids:
        return {}

    result = await session.execute(
        select(project_attachment)
        .where(project_attachment.c.project_id.in_(list(project_ids)))
        .order_by(
            project_attachment.c.created_at.desc(),
            project_attachment.c.id.desc(),
        )
    )
    rows = result.fetchall()
    user_map = await get_user_map(
        session, {row.created_by for row in rows if row.created_by}
    )

    attachments_map: Dict[int, List[ProjectAttachmentResponse]] = {}
    for row in rows:
        attachments_map.setdefault(row.project_id, []).append(
            ProjectAttachmentResponse(
                id=row.id,
                project_id=row.project_id,
                attachment_type=row.attachment_type,
                file_name=row.file_name,
                url_path=row.url_path,
                mime_type=row.mime_type,
                file_size=row.file_size,
                description=row.description,
                created_by=row.created_by,
                created_at=row.created_at,
                updated_at=row.updated_at,
                created_by_user=user_map.get(row.created_by),
            )
        )
    return attachments_map


async def get_project_attachment_or_404(session: AsyncSession, attachment_id: int):
    result = await session.execute(
        select(project_attachment).where(project_attachment.c.id == attachment_id)
    )
    attachment_row = result.fetchone()
    if not attachment_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project attachment topilmadi",
        )
    return attachment_row


async def build_board_detail(
    session: AsyncSession,
    board_row,
    scoped_user_id: Optional[int] = None,
) -> BoardDetailResponse:
    columns_result = await session.execute(
        select(project_board_column)
        .where(project_board_column.c.board_id == board_row.id)
        .order_by(project_board_column.c.order.asc(), project_board_column.c.id.asc())
    )
    column_rows = columns_result.fetchall()
    column_ids = [column.id for column in column_rows]

    card_rows = []
    if column_ids:
        cards_query = (
            select(project_board_card)
            .where(project_board_card.c.column_id.in_(column_ids))
            .order_by(project_board_card.c.column_id.asc(), project_board_card.c.order.asc())
        )
        if scoped_user_id is not None:
            cards_query = cards_query.where(build_card_user_filter(scoped_user_id))
        cards_result = await session.execute(cards_query)
        card_rows = cards_result.fetchall()

    assignee_ids_map = await get_card_assignee_ids_map(session, [card.id for card in card_rows])
    user_ids: Set[int] = set()
    if board_row.created_by:
        user_ids.add(board_row.created_by)
    for card_row in card_rows:
        if card_row.created_by:
            user_ids.add(card_row.created_by)
        user_ids.update(get_card_assignee_ids(card_row, assignee_ids_map))

    user_map = await get_user_map(session, user_ids)
    files_map = await get_card_files_map(session, [card.id for card in card_rows])

    cards_by_column: Dict[int, List[CardResponse]] = {}
    for card_row in card_rows:
        assignee_ids = get_card_assignee_ids(card_row, assignee_ids_map)
        cards_by_column.setdefault(card_row.column_id, []).append(
            CardResponse(
                id=card_row.id,
                column_id=card_row.column_id,
                title=card_row.title,
                description=card_row.description,
                order=card_row.order,
                priority=card_row.priority,
                assignee_id=assignee_ids[0] if assignee_ids else None,
                assignee_ids=assignee_ids,
                due_date=card_row.due_date,
                completed_at=card_row.completed_at,
                telegram_source_chat_id=card_row.telegram_source_chat_id,
                telegram_source_message_id=card_row.telegram_source_message_id,
                telegram_source_command=card_row.telegram_source_command,
                telegram_source_kind=card_row.telegram_source_kind,
                completion_duration_seconds=completion_duration_seconds(card_row),
                created_by=card_row.created_by,
                created_at=card_row.created_at,
                updated_at=card_row.updated_at,
                assignee=user_map.get(assignee_ids[0]) if assignee_ids else None,
                assignees=[user_map[assignee_id] for assignee_id in assignee_ids if assignee_id in user_map],
                created_by_user=user_map.get(card_row.created_by),
                files=files_map.get(card_row.id, []),
            )
        )

    return BoardDetailResponse(
        id=board_row.id,
        project_id=board_row.project_id,
        name=board_row.name,
        description=board_row.description,
        created_by=board_row.created_by,
        created_at=board_row.created_at,
        is_archived=board_row.is_archived,
        created_by_user=user_map.get(board_row.created_by),
        columns=[
            BoardColumnResponse(
                id=column_row.id,
                board_id=column_row.board_id,
                name=column_row.name,
                order=column_row.order,
                color=column_row.color,
                created_at=column_row.created_at,
                cards=cards_by_column.get(column_row.id, []),
            )
            for column_row in column_rows
        ],
    )


async def build_card_detail(session: AsyncSession, card_row) -> CardDetailResponse:
    column_row = await get_column_or_404(session, card_row.column_id)
    board_row = await get_board_or_404(session, column_row.board_id, include_archived=True)

    assignee_ids_map = await get_card_assignee_ids_map(session, [card_row.id])
    assignee_ids = get_card_assignee_ids(card_row, assignee_ids_map)
    user_ids = {user_id for user_id in [card_row.created_by, *assignee_ids] if user_id}
    user_map = await get_user_map(session, user_ids)
    files_map = await get_card_files_map(session, [card_row.id])
    status_history = await get_card_status_history_payload(session, card_row.id)

    return CardDetailResponse(
        id=card_row.id,
        board_id=board_row.id,
        project_id=board_row.project_id,
        column_id=card_row.column_id,
        title=card_row.title,
        description=card_row.description,
        order=card_row.order,
        priority=card_row.priority,
        assignee_id=assignee_ids[0] if assignee_ids else None,
        assignee_ids=assignee_ids,
        due_date=card_row.due_date,
        completed_at=card_row.completed_at,
        telegram_source_chat_id=card_row.telegram_source_chat_id,
        telegram_source_message_id=card_row.telegram_source_message_id,
        telegram_source_command=card_row.telegram_source_command,
        telegram_source_kind=card_row.telegram_source_kind,
        completion_duration_seconds=completion_duration_seconds(card_row),
        current_status_duration_seconds=await get_current_status_duration_seconds(session, card_row.id),
        created_by=card_row.created_by,
        created_at=card_row.created_at,
        updated_at=card_row.updated_at,
        assignee=user_map.get(assignee_ids[0]) if assignee_ids else None,
        assignees=[user_map[assignee_id] for assignee_id in assignee_ids if assignee_id in user_map],
        created_by_user=user_map.get(card_row.created_by),
        files=files_map.get(card_row.id, []),
        status_history=status_history,
    )


@router.on_event("startup")
async def warm_project_card_schema() -> None:
    await ensure_project_card_schema()


@router.get("/projects", response_model=ProjectListResponse, summary="Projectlar ro'yxati")
async def list_projects(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)
    result = await session.execute(
        select(project)
        .join(project_member, project_member.c.project_id == project.c.id)
        .where(project_member.c.user_id == current_user.id)
        .order_by(project.c.id.desc())
    )
    project_rows = result.fetchall()
    project_ids = [row.id for row in project_rows]
    member_counts, board_counts = await get_project_counts(session, project_ids)
    user_map = await get_user_map(
        session, {project_row.created_by for project_row in project_rows if project_row.created_by}
    )

    projects_payload = [
        ProjectSummaryResponse(
            id=project_row.id,
            team_id=project_row.team_id,
            project_name=project_row.project_name,
            project_description=project_row.project_description,
            project_url=project_row.project_url,
            project_image=project_row.project_image,
            deadline=project_row.deadline,
            telegram_group_id=project_row.telegram_group_id,
            created_by=project_row.created_by,
            created_at=project_row.created_at,
            updated_at=project_row.updated_at,
            member_count=member_counts.get(project_row.id, 0),
            board_count=board_counts.get(project_row.id, 0),
            created_by_user=user_map.get(project_row.created_by),
        )
        for project_row in project_rows
    ]

    return ProjectListResponse(projects=projects_payload, total_count=len(projects_payload))


@router.get("/projects/users/all", response_model=List[UserSummaryResponse], summary="Barcha userlar ro'yxati")
async def list_all_users_for_assignment(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)

    result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.email)
        .order_by(user.c.name.asc(), user.c.surname.asc(), user.c.id.asc())
    )

    return [
        UserSummaryResponse(
            id=row.id,
            name=row.name,
            surname=row.surname,
            email=row.email,
        )
        for row in result.fetchall()
    ]


@router.get("/projects/teams", response_model=List[ProjectTeamResponse], summary="Project teamlar")
async def list_project_teams(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)
    rows = (
        await session.execute(select(project_team).order_by(project_team.c.name.asc(), project_team.c.id.asc()))
    ).fetchall()
    teams = []
    for row in rows:
        team_response = await get_project_team_response(session, row.id)
        if team_response:
            teams.append(team_response)
    return teams


@router.post("/projects/teams", response_model=CreateResponse, summary="Project team yaratish")
async def create_project_team(
    team_data: ProjectTeamCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)
    result = await session.execute(
        insert(project_team)
        .values(
            name=team_data.name,
            description=team_data.description,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        .returning(project_team.c.id)
    )
    team_id = result.scalar_one()
    await sync_team_members(session, team_id, team_data.member_ids)
    await session.commit()
    return CreateResponse(message="Team yaratildi", id=team_id)


@router.get("/projects/teams/{team_id}", response_model=ProjectTeamResponse, summary="Project team detail")
async def get_project_team_detail(
    team_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)
    team_response = await get_project_team_response(session, team_id)
    if not team_response:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team topilmadi")
    return team_response


@router.patch("/projects/teams/{team_id}", response_model=SuccessResponse, summary="Project team yangilash")
async def update_project_team(
    team_id: int,
    team_data: ProjectTeamUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)
    await ensure_team_exists(session, team_id)
    update_values = team_data.model_dump(exclude_unset=True, exclude={"member_ids"})
    if update_values:
        update_values["updated_at"] = datetime.utcnow()
        await session.execute(update(project_team).where(project_team.c.id == team_id).values(**update_values))
    if team_data.member_ids is not None:
        await sync_team_members(session, team_id, team_data.member_ids)
    if not update_values and team_data.member_ids is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Yangilanadigan ma'lumot topilmadi")
    await session.commit()
    return SuccessResponse(message="Team yangilandi")


@router.delete("/projects/teams/{team_id}", response_model=SuccessResponse, summary="Project team o'chirish")
async def delete_project_team(
    team_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)
    await ensure_team_exists(session, team_id)
    await session.execute(delete(project_team).where(project_team.c.id == team_id))
    await session.commit()
    return SuccessResponse(message="Team o'chirildi")


@router.post("/projects", response_model=CreateResponse, summary="Yangi project yaratish")
async def create_project(
    project_name: str = Form(...),
    project_description: Optional[str] = Form(None),
    project_url: Optional[str] = Form(None),
    team_id: Optional[int] = Form(None),
    deadline: Optional[datetime] = Form(None),
    telegram_group_id: Optional[str] = Form(None),
    telegram_group_chat_id: Optional[str] = Form(None),
    member_ids: Optional[List[str]] = Form(None),
    image: Optional[UploadFile] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_projects_page_access(session, current_user)
    project_name = project_name.strip()
    if not project_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project nomi bo'sh bo'lishi mumkin emas",
        )

    parsed_member_ids = parse_member_ids_form(member_ids) or []
    parsed_member_ids.extend(await get_team_member_ids(session, team_id))
    validated_member_ids = await ensure_valid_member_ids(session, parsed_member_ids, current_user.id)
    await ensure_team_exists(session, team_id)
    image_path = await save_image(image, "project") if image else None

    result = await session.execute(
        insert(project)
        .values(
            project_name=project_name,
            team_id=team_id,
            project_description=project_description,
            project_url=project_url,
            project_image=image_path,
            deadline=normalize_project_datetime(deadline),
            telegram_group_id=(telegram_group_chat_id or telegram_group_id).strip() if (telegram_group_chat_id or telegram_group_id) else None,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        .returning(project.c.id)
    )
    project_id = result.scalar_one()

    await session.execute(
        insert(project_member).values(
            [
                {
                    "project_id": project_id,
                    "user_id": member_id,
                    "created_at": datetime.utcnow(),
                }
                for member_id in validated_member_ids
            ]
        )
    )
    await session.commit()

    return CreateResponse(message="Project muvaffaqiyatli yaratildi", id=project_id)


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse, summary="Project detail")
async def get_project_detail(
    project_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    project_row = await ensure_project_member_access(session, project_id, current_user)

    members_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.email)
        .join(project_member, project_member.c.user_id == user.c.id)
        .where(project_member.c.project_id == project_id)
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )
    boards_result = await session.execute(
        select(project_board)
        .where(project_board.c.project_id == project_id, project_board.c.is_archived == False)  # noqa: E712
        .order_by(project_board.c.id.desc())
    )
    board_rows = boards_result.fetchall()

    user_ids = {project_row.created_by} if project_row.created_by else set()
    user_ids.update({board_row.created_by for board_row in board_rows if board_row.created_by})
    user_map = await get_user_map(session, user_ids)
    attachments_map = await get_project_attachments_map(session, [project_id])

    members = [
        UserSummaryResponse(id=row.id, name=row.name, surname=row.surname, email=row.email)
        for row in members_result.fetchall()
    ]
    boards = [
        BoardListItemResponse(
            id=board_row.id,
            project_id=board_row.project_id,
            name=board_row.name,
            description=board_row.description,
            created_by=board_row.created_by,
            created_at=board_row.created_at,
            is_archived=board_row.is_archived,
            created_by_user=user_map.get(board_row.created_by),
        )
        for board_row in board_rows
    ]

    return ProjectDetailResponse(
        id=project_row.id,
        team_id=project_row.team_id,
        project_name=project_row.project_name,
        project_description=project_row.project_description,
        project_url=project_row.project_url,
        project_image=project_row.project_image,
        deadline=project_row.deadline,
        telegram_group_id=project_row.telegram_group_id,
        created_by=project_row.created_by,
        created_at=project_row.created_at,
        updated_at=project_row.updated_at,
        created_by_user=user_map.get(project_row.created_by),
        team=await get_project_team_response(session, project_row.team_id),
        members=members,
        boards=boards,
        attachments=attachments_map.get(project_id, []),
    )


@router.get(
    "/projects/{project_id}/boards/detail",
    response_model=ProjectBoardsDetailResponse,
    summary="Project boardlari columns va cardlar bilan",
)
async def get_project_boards_detail(
    project_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_project_member_access(session, project_id, current_user)

    boards_result = await session.execute(
        select(project_board)
        .where(
            project_board.c.project_id == project_id,
            project_board.c.is_archived == False,  # noqa: E712
        )
        .order_by(project_board.c.id.desc())
    )
    board_rows = boards_result.fetchall()
    board_details = [await build_board_detail(session, board_row) for board_row in board_rows]

    return ProjectBoardsDetailResponse(
        project_id=project_id,
        boards=board_details,
        total_count=len(board_details),
    )


@router.patch("/projects/{project_id}", response_model=SuccessResponse, summary="Projectni yangilash")
async def update_project(
    project_id: int,
    project_name: Optional[str] = Form(None),
    project_description: Optional[str] = Form(None),
    project_url: Optional[str] = Form(None),
    team_id: Optional[int] = Form(None),
    deadline: Optional[datetime] = Form(None),
    telegram_group_id: Optional[str] = Form(None),
    telegram_group_chat_id: Optional[str] = Form(None),
    member_ids: Optional[List[str]] = Form(None),
    image: Optional[UploadFile] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    project_row = await ensure_project_member_access(session, project_id, current_user)

    update_values = {}
    if project_name is not None:
        project_name = project_name.strip()
        if not project_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project nomi bo'sh bo'lishi mumkin emas",
            )
        update_values["project_name"] = project_name
    if project_description is not None:
        update_values["project_description"] = project_description
    if project_url is not None:
        update_values["project_url"] = project_url
    if team_id is not None:
        await ensure_team_exists(session, team_id)
        update_values["team_id"] = team_id
    if deadline is not None:
        update_values["deadline"] = normalize_project_datetime(deadline)
    resolved_telegram_group_id = telegram_group_chat_id if telegram_group_chat_id is not None else telegram_group_id
    if resolved_telegram_group_id is not None:
        update_values["telegram_group_id"] = resolved_telegram_group_id.strip() or None

    parsed_member_ids = parse_member_ids_form(member_ids)
    if parsed_member_ids is not None or team_id is not None:
        parsed_member_ids = parsed_member_ids or []
        target_team_id = team_id if team_id is not None else project_row.team_id
        parsed_member_ids.extend(await get_team_member_ids(session, target_team_id))
        parsed_member_ids = await ensure_valid_member_ids(session, parsed_member_ids, current_user.id)

    if image is not None:
        image_path = await save_image(image, "project")
        delete_image_if_exists(project_row.project_image)
        update_values["project_image"] = image_path

    if not update_values and parsed_member_ids is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi",
        )

    if update_values:
        update_values["updated_at"] = datetime.utcnow()
        await session.execute(
            update(project).where(project.c.id == project_id).values(**update_values)
        )

    if parsed_member_ids is not None:
        await session.execute(delete(project_member).where(project_member.c.project_id == project_id))
        await session.execute(
            insert(project_member).values(
                [
                    {
                        "project_id": project_id,
                        "user_id": member_id,
                        "created_at": datetime.utcnow(),
                    }
                    for member_id in parsed_member_ids
                ]
            )
        )

    await session.commit()
    return SuccessResponse(message="Project muvaffaqiyatli yangilandi")


@router.patch("/projects/{project_id}/telegram-group", response_model=SuccessResponse, summary="Project Telegram group bog'lash")
async def update_project_telegram_group(
    project_id: int,
    group_data: ProjectTelegramGroupRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_project_member_access(session, project_id, current_user)
    await session.execute(
        update(project)
        .where(project.c.id == project_id)
        .values(
            telegram_group_id=(group_data.telegram_group_chat_id or group_data.telegram_group_id).strip() if (group_data.telegram_group_chat_id or group_data.telegram_group_id) else None,
            updated_at=datetime.utcnow(),
        )
    )
    await session.commit()
    return SuccessResponse(message="Project Telegram group yangilandi")


@router.delete("/projects/{project_id}", response_model=SuccessResponse, summary="Projectni o'chirish")
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    project_row = await ensure_project_member_access(session, project_id, current_user)

    card_files_result = await session.execute(
        select(project_board_card_file.c.url_path)
        .select_from(
            project_board_card_file
            .join(project_board_card, project_board_card_file.c.card_id == project_board_card.c.id)
            .join(project_board_column, project_board_card.c.column_id == project_board_column.c.id)
            .join(project_board, project_board_column.c.board_id == project_board.c.id)
        )
        .where(project_board.c.project_id == project_id)
    )
    for file_row in card_files_result.fetchall():
        delete_image_if_exists(file_row.url_path)

    attachment_files_result = await session.execute(
        select(project_attachment.c.url_path).where(project_attachment.c.project_id == project_id)
    )
    for file_row in attachment_files_result.fetchall():
        delete_file_if_exists(file_row.url_path)

    delete_image_if_exists(project_row.project_image)
    await session.execute(delete(project).where(project.c.id == project_id))
    await session.commit()
    return SuccessResponse(message=f"Project '{project_row.project_name}' o'chirildi")


@router.post(
    "/projects/{project_id}/attachments",
    response_model=ProjectAttachmentResponse,
    summary="Projectga attachment yuklash",
)
async def create_project_attachment(
    project_id: int,
    attachment_type: ProjectAttachmentType = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_project_member_access(session, project_id, current_user)
    url_path, file_name, file_size, mime_type = await save_project_attachment_file(file, project_id)
    created_at = datetime.utcnow()

    result = await session.execute(
        insert(project_attachment)
        .values(
            project_id=project_id,
            attachment_type=attachment_type,
            file_name=file_name,
            url_path=url_path,
            mime_type=mime_type,
            file_size=file_size,
            description=description,
            created_by=current_user.id,
            created_at=created_at,
            updated_at=created_at,
        )
        .returning(project_attachment.c.id)
    )
    attachment_id = result.scalar_one()
    await session.commit()
    attachment_row = await get_project_attachment_or_404(session, attachment_id)
    user_map = await get_user_map(session, {current_user.id})
    return ProjectAttachmentResponse(
        id=attachment_row.id,
        project_id=attachment_row.project_id,
        attachment_type=attachment_row.attachment_type,
        file_name=attachment_row.file_name,
        url_path=attachment_row.url_path,
        mime_type=attachment_row.mime_type,
        file_size=attachment_row.file_size,
        description=attachment_row.description,
        created_by=attachment_row.created_by,
        created_at=attachment_row.created_at,
        updated_at=attachment_row.updated_at,
        created_by_user=user_map.get(attachment_row.created_by),
    )


@router.get(
    "/projects/{project_id}/attachments",
    response_model=List[ProjectAttachmentResponse],
    summary="Project attachmentlari ro'yxati",
)
async def list_project_attachments(
    project_id: int,
    attachment_type: Optional[ProjectAttachmentType] = Query(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_project_member_access(session, project_id, current_user)
    attachments_map = await get_project_attachments_map(session, [project_id])
    items = attachments_map.get(project_id, [])
    if attachment_type is not None:
        items = [item for item in items if item.attachment_type == attachment_type]
    return items


@router.get(
    "/projects/attachments/{attachment_id}",
    response_model=ProjectAttachmentResponse,
    summary="Bitta project attachment detail",
)
async def get_project_attachment_detail(
    attachment_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    attachment_row = await get_project_attachment_or_404(session, attachment_id)
    await ensure_project_member_access(session, attachment_row.project_id, current_user)
    user_map = await get_user_map(
        session, {attachment_row.created_by} if attachment_row.created_by else set()
    )
    return ProjectAttachmentResponse(
        id=attachment_row.id,
        project_id=attachment_row.project_id,
        attachment_type=attachment_row.attachment_type,
        file_name=attachment_row.file_name,
        url_path=attachment_row.url_path,
        mime_type=attachment_row.mime_type,
        file_size=attachment_row.file_size,
        description=attachment_row.description,
        created_by=attachment_row.created_by,
        created_at=attachment_row.created_at,
        updated_at=attachment_row.updated_at,
        created_by_user=user_map.get(attachment_row.created_by),
    )


@router.patch(
    "/projects/attachments/{attachment_id}",
    response_model=ProjectAttachmentResponse,
    summary="Project attachmentni yangilash",
)
async def update_project_attachment(
    attachment_id: int,
    attachment_type: Optional[ProjectAttachmentType] = Form(None),
    description: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    attachment_row = await get_project_attachment_or_404(session, attachment_id)
    await ensure_project_member_access(session, attachment_row.project_id, current_user)

    update_values = {}
    if attachment_type is not None:
        update_values["attachment_type"] = attachment_type
    if description is not None:
        update_values["description"] = description
    if file is not None:
        url_path, file_name, file_size, mime_type = await save_project_attachment_file(
            file, attachment_row.project_id
        )
        delete_file_if_exists(attachment_row.url_path)
        update_values.update(
            {
                "url_path": url_path,
                "file_name": file_name,
                "file_size": file_size,
                "mime_type": mime_type,
            }
        )

    if not update_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan attachment ma'lumoti topilmadi",
        )

    update_values["updated_at"] = datetime.utcnow()
    await session.execute(
        update(project_attachment)
        .where(project_attachment.c.id == attachment_id)
        .values(**update_values)
    )
    await session.commit()
    updated_row = await get_project_attachment_or_404(session, attachment_id)
    user_map = await get_user_map(
        session, {updated_row.created_by} if updated_row.created_by else set()
    )
    return ProjectAttachmentResponse(
        id=updated_row.id,
        project_id=updated_row.project_id,
        attachment_type=updated_row.attachment_type,
        file_name=updated_row.file_name,
        url_path=updated_row.url_path,
        mime_type=updated_row.mime_type,
        file_size=updated_row.file_size,
        description=updated_row.description,
        created_by=updated_row.created_by,
        created_at=updated_row.created_at,
        updated_at=updated_row.updated_at,
        created_by_user=user_map.get(updated_row.created_by),
    )


@router.delete(
    "/projects/attachments/{attachment_id}",
    response_model=SuccessResponse,
    summary="Project attachmentni o'chirish",
)
async def delete_project_attachment(
    attachment_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    attachment_row = await get_project_attachment_or_404(session, attachment_id)
    await ensure_project_member_access(session, attachment_row.project_id, current_user)
    delete_file_if_exists(attachment_row.url_path)
    await session.execute(delete(project_attachment).where(project_attachment.c.id == attachment_id))
    await session.commit()
    return SuccessResponse(message="Project attachment o'chirildi")


@router.get("/projects/{project_id}/boards", response_model=BoardListResponse, summary="Project boardlari")
async def list_project_boards(
    project_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_project_member_access(session, project_id, current_user)
    result = await session.execute(
        select(project_board)
        .where(project_board.c.project_id == project_id, project_board.c.is_archived == False)  # noqa: E712
        .order_by(project_board.c.id.desc())
    )
    board_rows = result.fetchall()
    user_map = await get_user_map(
        session, {board_row.created_by for board_row in board_rows if board_row.created_by}
    )

    boards = [
        BoardListItemResponse(
            id=board_row.id,
            project_id=board_row.project_id,
            name=board_row.name,
            description=board_row.description,
            created_by=board_row.created_by,
            created_at=board_row.created_at,
            is_archived=board_row.is_archived,
            created_by_user=user_map.get(board_row.created_by),
        )
        for board_row in board_rows
    ]
    return BoardListResponse(boards=boards, total_count=len(boards))


@router.post("/projects/{project_id}/boards", response_model=CreateResponse, summary="Board yaratish")
async def create_board(
    project_id: int,
    board_data: BoardCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_project_member_access(session, project_id, current_user)

    result = await session.execute(
        insert(project_board)
        .values(
            project_id=project_id,
            name=board_data.name,
            description=board_data.description,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            is_archived=False,
        )
        .returning(project_board.c.id)
    )
    board_id = result.scalar_one()

    await session.execute(
        insert(project_board_column).values(
            [
                {
                    "board_id": board_id,
                    "name": column_data["name"],
                    "order": index,
                    "color": column_data["color"],
                    "created_at": datetime.utcnow(),
                }
                for index, column_data in enumerate(DEFAULT_BOARD_COLUMNS)
            ]
        )
    )
    await session.commit()

    return CreateResponse(message="Board yaratildi va default columnlar qo'shildi", id=board_id)


@router.get("/boards/{board_id}", response_model=BoardDetailResponse, summary="Board detail")
async def get_board_detail(
    board_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    board_row = await get_board_or_404(session, board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)
    return await build_board_detail(session, board_row)


@router.patch("/boards/{board_id}", response_model=SuccessResponse, summary="Boardni yangilash")
async def update_board(
    board_id: int,
    board_data: BoardUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    board_row = await get_board_or_404(session, board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)

    update_values = board_data.model_dump(exclude_unset=True)
    if not update_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi",
        )

    await session.execute(update(project_board).where(project_board.c.id == board_id).values(**update_values))
    await session.commit()
    return SuccessResponse(message="Board muvaffaqiyatli yangilandi")


@router.delete("/boards/{board_id}", response_model=SuccessResponse, summary="Boardni archive qilish")
async def archive_board(
    board_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    board_row = await get_board_or_404(session, board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)
    await session.execute(
        update(project_board).where(project_board.c.id == board_id).values(is_archived=True)
    )
    await session.commit()
    return SuccessResponse(message="Board archive qilindi")


@router.post("/boards/{board_id}/columns", response_model=CreateResponse, summary="Column qo'shish")
async def create_column(
    board_id: int,
    column_data: ColumnCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    board_row = await get_board_or_404(session, board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)

    count_result = await session.execute(
        select(func.count(project_board_column.c.id)).where(project_board_column.c.board_id == board_id)
    )
    next_order = int(count_result.scalar() or 0)

    result = await session.execute(
        insert(project_board_column)
        .values(
            board_id=board_id,
            name=column_data.name,
            order=next_order,
            color=column_data.color,
            created_at=datetime.utcnow(),
        )
        .returning(project_board_column.c.id)
    )
    column_id = result.scalar_one()
    await session.commit()

    return CreateResponse(message="Column muvaffaqiyatli yaratildi", id=column_id)


@router.patch("/columns/{column_id}", response_model=SuccessResponse, summary="Columnni yangilash")
async def update_column(
    column_id: int,
    column_data: ColumnUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    column_row = await get_column_or_404(session, column_id)
    board_row = await get_board_or_404(session, column_row.board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)

    update_values = column_data.model_dump(exclude_unset=True)
    if not update_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi",
        )

    await session.execute(
        update(project_board_column).where(project_board_column.c.id == column_id).values(**update_values)
    )
    await session.commit()
    return SuccessResponse(message="Column muvaffaqiyatli yangilandi")


@router.patch("/columns/{column_id}/move", response_model=SuccessResponse, summary="Columnni ko'chirish")
async def move_column(
    column_id: int,
    move_data: ColumnMoveRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    column_row = await get_column_or_404(session, column_id)
    board_row = await get_board_or_404(session, column_row.board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)

    siblings_result = await session.execute(
        select(project_board_column.c.id)
        .where(project_board_column.c.board_id == board_row.id)
        .order_by(project_board_column.c.order.asc(), project_board_column.c.id.asc())
    )
    sibling_ids = [row.id for row in siblings_result.fetchall()]
    sibling_ids.remove(column_id)
    sibling_ids.insert(clamp_position(move_data.order, len(sibling_ids)), column_id)
    await resequence_columns(session, sibling_ids)
    await session.commit()

    return SuccessResponse(message="Column order muvaffaqiyatli yangilandi")


@router.delete("/columns/{column_id}", response_model=SuccessResponse, summary="Columnni o'chirish")
async def delete_column(
    column_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    column_row = await get_column_or_404(session, column_id)
    board_row = await get_board_or_404(session, column_row.board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)

    cards_count_result = await session.execute(
        select(func.count(project_board_card.c.id)).where(project_board_card.c.column_id == column_id)
    )
    cards_count = int(cards_count_result.scalar() or 0)
    if cards_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Column ichida cardlar bor. Avval ularni boshqa column ga ko'chiring",
        )

    await session.execute(delete(project_board_column).where(project_board_column.c.id == column_id))

    siblings_result = await session.execute(
        select(project_board_column.c.id)
        .where(project_board_column.c.board_id == board_row.id)
        .order_by(project_board_column.c.order.asc(), project_board_column.c.id.asc())
    )
    sibling_ids = [row.id for row in siblings_result.fetchall()]
    await resequence_columns(session, sibling_ids)
    await session.commit()

    return SuccessResponse(message="Column muvaffaqiyatli o'chirildi")


@router.post("/columns/{column_id}/cards", response_model=CreateResponse, summary="Card yaratish")
async def create_card(
    column_id: int,
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    order: Optional[int] = Form(None),
    priority: str = Form("medium"),
    assignee_id: Optional[int] = Form(None),
    assignee_ids: Optional[List[str]] = Form(None),
    due_date: Optional[datetime] = Form(None),
    images: Optional[List[UploadFile]] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    column_row = await get_column_or_404(session, column_id)
    board_row = await get_board_or_404(session, column_row.board_id)
    await ensure_projects_page_access(session, current_user)
    title = title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Card title bo'sh bo'lishi mumkin emas",
        )

    try:
        priority_value = CardPriority(priority.strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Priority faqat low, medium yoki high bo'lishi kerak",
        )

    resolved_assignee_ids = resolve_assignee_input(
        assignee_id=assignee_id,
        assignee_ids=parse_assignee_ids_form(assignee_ids),
    )
    validated_assignee_ids = await ensure_user_ids_exist(
        session,
        resolved_assignee_ids or [],
    )
    normalized_due_date = normalize_project_datetime(due_date)

    siblings_result = await session.execute(
        select(project_board_card.c.id)
        .where(project_board_card.c.column_id == column_id)
        .order_by(project_board_card.c.order.asc(), project_board_card.c.id.asc())
    )
    sibling_ids = [row.id for row in siblings_result.fetchall()]
    insert_order = len(sibling_ids)
    target_order = clamp_position(
        order if order is not None else insert_order,
        len(sibling_ids),
    )

    result = await session.execute(
        insert(project_board_card)
        .values(
            column_id=column_id,
            title=title,
            description=description,
            order=insert_order,
            priority=priority_value,
            assignee_id=validated_assignee_ids[0] if validated_assignee_ids else None,
            due_date=normalized_due_date,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        .returning(project_board_card.c.id)
    )
    card_id = result.scalar_one()

    await sync_card_assignees(session, card_id, validated_assignee_ids)
    await open_card_status_history(session, card_id, column_row.id, column_row.name, current_user.id)
    await save_card_images(session, card_id, images)
    sibling_ids.insert(target_order, card_id)
    await resequence_cards(session, sibling_ids)
    await session.commit()

    if validated_assignee_ids:
        assignee_result = await session.execute(
            select(user.c.id, user.c.chat_id).where(user.c.id.in_(validated_assignee_ids))
        )
        assignee_chat_map = {row.id: row.chat_id for row in assignee_result.fetchall() if row.chat_id}
        project_result = await session.execute(
            select(project.c.project_name).where(project.c.id == board_row.project_id)
        )
        project_row = project_result.fetchone()
        assigner_name = f"{current_user.name} {current_user.surname}".strip()
        for target_assignee_id in validated_assignee_ids:
            chat_id = assignee_chat_map.get(target_assignee_id)
            if not chat_id:
                continue
            background_tasks.add_task(
                send_card_assignment_notification,
                chat_id,
                title,
                description,
                priority,
                normalized_due_date,
                assigner_name,
                project_row.project_name if project_row else None,
                column_row.name,
            )

    return CreateResponse(message="Card muvaffaqiyatli yaratildi", id=card_id)


@router.get("/cards/{card_id}", response_model=CardDetailResponse, summary="Card detail")
async def get_card_detail(
    card_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    card_row = await get_card_or_404(session, card_id)
    await ensure_card_access(session, card_row, current_user)
    return await build_card_detail(session, card_row)


@router.get("/open/projects/user/{user_id}", response_model=ProjectListResponse, summary="Ochiq user projectlari")
async def open_list_projects_by_user(
    user_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_user_exists(session, user_id, "User topilmadi")

    result = await session.execute(
        select(project)
        .distinct()
        .outerjoin(project_member, project_member.c.project_id == project.c.id)
        .outerjoin(project_board, project_board.c.project_id == project.c.id)
        .outerjoin(project_board_column, project_board_column.c.board_id == project_board.c.id)
        .outerjoin(project_board_card, project_board_card.c.column_id == project_board_column.c.id)
        .where(
            (project_member.c.user_id == user_id)
            | build_card_user_filter(user_id)
        )
        .order_by(project.c.id.desc())
    )
    project_rows = result.fetchall()
    project_ids = [row.id for row in project_rows]
    member_counts, board_counts = await get_project_counts(session, project_ids)
    user_map = await get_user_map(
        session, {project_row.created_by for project_row in project_rows if project_row.created_by}
    )

    projects_payload = [
        ProjectSummaryResponse(
            id=project_row.id,
            team_id=project_row.team_id,
            project_name=project_row.project_name,
            project_description=project_row.project_description,
            project_url=project_row.project_url,
            project_image=project_row.project_image,
            deadline=project_row.deadline,
            telegram_group_id=project_row.telegram_group_id,
            created_by=project_row.created_by,
            created_at=project_row.created_at,
            updated_at=project_row.updated_at,
            member_count=member_counts.get(project_row.id, 0),
            board_count=board_counts.get(project_row.id, 0),
            created_by_user=user_map.get(project_row.created_by),
        )
        for project_row in project_rows
    ]
    return ProjectListResponse(projects=projects_payload, total_count=len(projects_payload))


@router.get(
    "/open/projects/{project_id}/detail/user/{user_id}",
    response_model=ProjectDetailResponse,
    summary="Ochiq user project detail",
)
async def open_get_project_detail_by_user(
    project_id: int,
    user_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_user_exists(session, user_id, "User topilmadi")
    project_row = await ensure_project_visible_for_user(session, project_id, user_id)

    members_result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.email)
        .join(project_member, project_member.c.user_id == user.c.id)
        .where(project_member.c.project_id == project_id)
        .order_by(user.c.name.asc(), user.c.surname.asc())
    )
    boards_result = await session.execute(
        select(project_board)
        .where(project_board.c.project_id == project_id, project_board.c.is_archived == False)  # noqa: E712
        .order_by(project_board.c.id.desc())
    )
    board_rows = boards_result.fetchall()

    user_ids = {project_row.created_by} if project_row.created_by else set()
    user_ids.update({board_row.created_by for board_row in board_rows if board_row.created_by})
    user_map = await get_user_map(session, user_ids)
    attachments_map = await get_project_attachments_map(session, [project_id])

    members = [
        UserSummaryResponse(id=row.id, name=row.name, surname=row.surname, email=row.email)
        for row in members_result.fetchall()
    ]
    boards = [
        BoardListItemResponse(
            id=board_row.id,
            project_id=board_row.project_id,
            name=board_row.name,
            description=board_row.description,
            created_by=board_row.created_by,
            created_at=board_row.created_at,
            is_archived=board_row.is_archived,
            created_by_user=user_map.get(board_row.created_by),
        )
        for board_row in board_rows
    ]

    return ProjectDetailResponse(
        id=project_row.id,
        team_id=project_row.team_id,
        project_name=project_row.project_name,
        project_description=project_row.project_description,
        project_url=project_row.project_url,
        project_image=project_row.project_image,
        deadline=project_row.deadline,
        telegram_group_id=project_row.telegram_group_id,
        created_by=project_row.created_by,
        created_at=project_row.created_at,
        updated_at=project_row.updated_at,
        created_by_user=user_map.get(project_row.created_by),
        team=await get_project_team_response(session, project_row.team_id),
        members=members,
        boards=boards,
        attachments=attachments_map.get(project_id, []),
    )


@router.get(
    "/open/projects/{project_id}/boards/detail/user/{user_id}",
    response_model=ProjectBoardsDetailResponse,
    summary="Ochiq user board detail",
)
async def open_get_project_boards_detail_by_user(
    project_id: int,
    user_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_user_exists(session, user_id, "User topilmadi")
    await ensure_project_visible_for_user(session, project_id, user_id)

    boards_result = await session.execute(
        select(project_board)
        .where(
            project_board.c.project_id == project_id,
            project_board.c.is_archived == False,  # noqa: E712
        )
        .order_by(project_board.c.id.desc())
    )
    board_rows = boards_result.fetchall()
    board_details = [await build_board_detail(session, board_row, user_id) for board_row in board_rows]

    return ProjectBoardsDetailResponse(
        project_id=project_id,
        boards=board_details,
        total_count=len(board_details),
    )


@router.get("/open/cards/user/{user_id}", response_model=CardListResponse, summary="Ochiq user cardlari")
async def open_list_cards_by_user(
    user_id: int,
    project_id: Optional[int] = Query(None, description="Faqat bitta project bo'yicha filter"),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_user_exists(session, user_id, "User topilmadi")

    if project_id is not None:
        await ensure_project_visible_for_user(session, project_id, user_id)

    cards_query = (
        select(
            project_board_card,
            project_board_column.c.board_id.label("board_id"),
            project_board_column.c.name.label("column_name"),
            project_board.c.project_id.label("project_id"),
            project_board.c.name.label("board_name"),
            project.c.project_name.label("project_name"),
        )
        .select_from(
            project_board_card
            .join(project_board_column, project_board_card.c.column_id == project_board_column.c.id)
            .join(project_board, project_board_column.c.board_id == project_board.c.id)
            .join(project, project_board.c.project_id == project.c.id)
        )
        .where(build_card_user_filter(user_id))
        .order_by(project_board_card.c.updated_at.desc(), project_board_card.c.id.desc())
    )

    if project_id is not None:
        cards_query = cards_query.where(project.c.id == project_id)

    result = await session.execute(cards_query)
    card_rows = result.fetchall()

    assignee_ids_map = await get_card_assignee_ids_map(session, [row.id for row in card_rows])
    user_ids: Set[int] = set()
    for row in card_rows:
        if row.created_by:
            user_ids.add(row.created_by)
        user_ids.update(get_card_assignee_ids(row, assignee_ids_map))
    user_map = await get_user_map(session, user_ids)
    files_map = await get_card_files_map(session, [row.id for row in card_rows])

    cards_payload = []
    for row in card_rows:
        assignee_ids = get_card_assignee_ids(row, assignee_ids_map)
        cards_payload.append(
            CardListItemResponse(
                id=row.id,
                board_id=row.board_id,
                project_id=row.project_id,
                column_id=row.column_id,
                title=row.title,
                description=row.description,
                order=row.order,
                priority=row.priority,
                assignee_id=assignee_ids[0] if assignee_ids else None,
                assignee_ids=assignee_ids,
                due_date=row.due_date,
                completed_at=row.completed_at,
                completion_duration_seconds=completion_duration_seconds(row),
                created_by=row.created_by,
                created_at=row.created_at,
                updated_at=row.updated_at,
                assignee=user_map.get(assignee_ids[0]) if assignee_ids else None,
                assignees=[user_map[assignee_user_id] for assignee_user_id in assignee_ids if assignee_user_id in user_map],
                created_by_user=user_map.get(row.created_by),
                files=files_map.get(row.id, []),
                project_name=row.project_name,
                board_name=row.board_name,
                column_name=row.column_name,
            )
        )

    return CardListResponse(cards=cards_payload, total_count=len(cards_payload))


@router.get(
    "/open/cards/{card_id}/user/{user_id}",
    response_model=CardDetailResponse,
    summary="Ochiq user card detail",
)
async def open_get_card_detail_by_user(
    card_id: int,
    user_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    await ensure_user_exists(session, user_id, "User topilmadi")

    card_row = await get_card_or_404(session, card_id)
    assignee_ids_map = await get_card_assignee_ids_map(session, [card_row.id])
    visible_assignee_ids = set(get_card_assignee_ids(card_row, assignee_ids_map))
    if not (user_id in visible_assignee_ids or (not visible_assignee_ids and card_row.created_by == user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bu userga tegishli card topilmadi")

    return await build_card_detail(session, card_row)


@router.patch("/cards/{card_id}", response_model=SuccessResponse, summary="Cardni yangilash")
async def update_card(
    card_id: int,
    background_tasks: BackgroundTasks,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    priority: Optional[str] = Form(None),
    assignee_id: Optional[int] = Form(None),
    assignee_ids: Optional[List[str]] = Form(None),
    due_date: Optional[datetime] = Form(None),
    clear_existing_images: bool = Form(False),
    images: Optional[List[UploadFile]] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    card_row = await get_card_or_404(session, card_id)
    column_row, board_row = await ensure_card_access(session, card_row, current_user)
    old_assignee_ids_map = await get_card_assignee_ids_map(session, [card_row.id])
    old_assignee_ids = get_card_assignee_ids(card_row, old_assignee_ids_map)

    update_values = {}
    if title is not None:
        title = title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Card title bo'sh bo'lishi mumkin emas",
            )
        update_values["title"] = title
    if description is not None:
        update_values["description"] = description
    if priority is not None:
        try:
            update_values["priority"] = CardPriority(priority.strip().lower())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Priority faqat low, medium yoki high bo'lishi kerak",
            )
    if due_date is not None:
        update_values["due_date"] = normalize_project_datetime(due_date)

    resolved_assignee_ids = resolve_assignee_input(
        assignee_id=assignee_id,
        assignee_ids=parse_assignee_ids_form(assignee_ids),
    )

    if not update_values and resolved_assignee_ids is None and not clear_existing_images and not images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi",
        )

    validated_assignee_ids: Optional[List[int]] = None
    if resolved_assignee_ids is not None:
        validated_assignee_ids = await ensure_user_ids_exist(session, resolved_assignee_ids)
        update_values["assignee_id"] = validated_assignee_ids[0] if validated_assignee_ids else None

    if update_values:
        update_values["updated_at"] = datetime.utcnow()
        await session.execute(
            update(project_board_card).where(project_board_card.c.id == card_id).values(**update_values)
        )

    if validated_assignee_ids is not None:
        await sync_card_assignees(session, card_id, validated_assignee_ids)

    if clear_existing_images:
        existing_files_result = await session.execute(
            select(project_board_card_file).where(project_board_card_file.c.card_id == card_id)
        )
        existing_files = existing_files_result.fetchall()
        delete_card_file_paths(existing_files)
        await session.execute(
            delete(project_board_card_file).where(project_board_card_file.c.card_id == card_id)
        )

    await save_card_images(session, card_id, images)
    await session.commit()

    new_assignee_ids = validated_assignee_ids if validated_assignee_ids is not None else old_assignee_ids
    added_assignee_ids = [assignee for assignee in new_assignee_ids if assignee not in old_assignee_ids]
    if added_assignee_ids:
        assignee_result = await session.execute(
            select(user.c.id, user.c.chat_id).where(user.c.id.in_(added_assignee_ids))
        )
        assignee_chat_map = {row.id: row.chat_id for row in assignee_result.fetchall() if row.chat_id}
        project_result = await session.execute(
            select(project.c.project_name).where(project.c.id == board_row.project_id)
        )
        project_row = project_result.fetchone()
        assigner_name = f"{current_user.name} {current_user.surname}".strip()
        card_title = update_values.get("title", card_row.title)
        card_description = update_values.get("description", card_row.description)
        card_priority = str(update_values.get("priority", card_row.priority).value
                            if hasattr(update_values.get("priority", card_row.priority), "value")
                            else update_values.get("priority", card_row.priority))
        card_due_date = update_values.get("due_date", card_row.due_date)
        for target_assignee_id in added_assignee_ids:
            chat_id = assignee_chat_map.get(target_assignee_id)
            if not chat_id:
                continue
            background_tasks.add_task(
                send_card_assignment_notification,
                chat_id,
                card_title,
                card_description,
                card_priority,
                card_due_date,
                assigner_name,
                project_row.project_name if project_row else None,
                column_row.name,
            )

    return SuccessResponse(message="Card muvaffaqiyatli yangilandi")


@router.patch("/cards/{card_id}/move", response_model=SuccessResponse, summary="Cardni ko'chirish")
async def move_card(
    card_id: int,
    move_data: CardMoveRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    card_row = await get_card_or_404(session, card_id)
    source_column, source_board = await ensure_card_access(session, card_row, current_user)

    target_column = await get_column_or_404(session, move_data.column_id)
    target_board = await get_board_or_404(session, target_column.board_id)
    if source_board.id != target_board.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cardni boshqa board ga ko'chirish mumkin emas",
        )

    if source_column.id == target_column.id:
        siblings_result = await session.execute(
            select(project_board_card.c.id)
            .where(project_board_card.c.column_id == source_column.id)
            .order_by(project_board_card.c.order.asc(), project_board_card.c.id.asc())
        )
        sibling_ids = [row.id for row in siblings_result.fetchall()]
        sibling_ids.remove(card_id)
        sibling_ids.insert(clamp_position(move_data.order, len(sibling_ids)), card_id)
        await resequence_cards(session, sibling_ids)
    else:
        source_result = await session.execute(
            select(project_board_card.c.id)
            .where(project_board_card.c.column_id == source_column.id)
            .order_by(project_board_card.c.order.asc(), project_board_card.c.id.asc())
        )
        target_result = await session.execute(
            select(project_board_card.c.id)
            .where(project_board_card.c.column_id == target_column.id)
            .order_by(project_board_card.c.order.asc(), project_board_card.c.id.asc())
        )
        source_ids = [row.id for row in source_result.fetchall()]
        target_ids = [row.id for row in target_result.fetchall()]
        source_ids.remove(card_id)

        await session.execute(
            update(project_board_card)
            .where(project_board_card.c.id == card_id)
            .values(
                column_id=target_column.id,
                order=-(card_id + 1000),
                updated_at=datetime.utcnow(),
            )
        )

        target_ids.insert(clamp_position(move_data.order, len(target_ids)), card_id)
        await resequence_cards(session, source_ids)
        await resequence_cards(session, target_ids)
        await close_open_card_status_history(session, card_id)
        await open_card_status_history(session, card_id, target_column.id, target_column.name, current_user.id)
        complete_values = {}
        if is_done_column_name(target_column.name) and card_row.completed_at is None:
            complete_values["completed_at"] = datetime.utcnow()
        elif not is_done_column_name(target_column.name):
            complete_values["completed_at"] = None
        if complete_values:
            await session.execute(
                update(project_board_card)
                .where(project_board_card.c.id == card_id)
                .values(**complete_values)
            )
        await write_task_move_daily_update(session, current_user.id, card_row.title, source_column.name, target_column.name)

    await session.execute(
        update(project_board_card)
        .where(project_board_card.c.id == card_id)
        .values(updated_at=datetime.utcnow())
    )
    await session.commit()

    return SuccessResponse(message="Card muvaffaqiyatli ko'chirildi")


@router.delete("/cards/{card_id}", response_model=SuccessResponse, summary="Cardni o'chirish")
async def delete_card(
    card_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_project_card_schema()
    card_row = await get_card_or_404(session, card_id)
    column_row, _board_row = await ensure_card_access(session, card_row, current_user)

    files_result = await session.execute(
        select(project_board_card_file).where(project_board_card_file.c.card_id == card_id)
    )
    delete_card_file_paths(files_result.fetchall())

    await session.execute(delete(project_board_card).where(project_board_card.c.id == card_id))

    siblings_result = await session.execute(
        select(project_board_card.c.id)
        .where(project_board_card.c.column_id == column_row.id)
        .order_by(project_board_card.c.order.asc(), project_board_card.c.id.asc())
    )
    sibling_ids = [row.id for row in siblings_result.fetchall()]
    await resequence_cards(session, sibling_ids)
    await session.commit()

    return SuccessResponse(message="Card muvaffaqiyatli o'chirildi")
