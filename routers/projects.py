from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_active_user
from database import get_async_session
from models.projects_models import (
    CardPriority,
    project,
    project_member,
    project_board,
    project_board_column,
    project_board_card,
    project_board_card_file,
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
    ColumnCreateRequest,
    ColumnMoveRequest,
    ColumnUpdateRequest,
    ProjectBoardsDetailResponse,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectSummaryResponse,
    UserSummaryResponse,
)
from schemes.schemes_users import CreateResponse, SuccessResponse
from utils.file_storage import delete_image_if_exists, save_image

router = APIRouter(tags=["Projects"])

DEFAULT_BOARD_COLUMNS = [
    {"name": "To Do", "color": "#64748B"},
    {"name": "Doing", "color": "#0EA5E9"},
    {"name": "Done", "color": "#22C55E"},
    {"name": "To Test", "color": "#F59E0B"},
    {"name": "Refix", "color": "#EF4444"},
]


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


async def ensure_project_member_access(session: AsyncSession, project_id: int, current_user):
    await ensure_projects_page_access(session, current_user)
    project_row = await get_project_or_404(session, project_id)
    membership = await session.execute(
        select(project_member.c.id).where(
            project_member.c.project_id == project_id,
            project_member.c.user_id == current_user.id,
        )
    )
    if not membership.fetchone():
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


def build_card_user_filter(target_user_id: int):
    return (project_board_card.c.assignee_id == target_user_id) | (
        project_board_card.c.assignee_id.is_(None) & (project_board_card.c.created_by == target_user_id)
    )


async def ensure_project_visible_for_user(session: AsyncSession, project_id: int, user_id: int):
    project_row = await get_project_or_404(session, project_id)

    membership = await session.execute(
        select(project_member.c.id).where(
            project_member.c.project_id == project_id,
            project_member.c.user_id == user_id,
        )
    )
    if membership.fetchone():
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

    user_ids: Set[int] = set()
    if board_row.created_by:
        user_ids.add(board_row.created_by)
    for card_row in card_rows:
        if card_row.created_by:
            user_ids.add(card_row.created_by)
        if card_row.assignee_id:
            user_ids.add(card_row.assignee_id)

    user_map = await get_user_map(session, user_ids)
    files_map = await get_card_files_map(session, [card.id for card in card_rows])

    cards_by_column: Dict[int, List[CardResponse]] = {}
    for card_row in card_rows:
        cards_by_column.setdefault(card_row.column_id, []).append(
            CardResponse(
                id=card_row.id,
                column_id=card_row.column_id,
                title=card_row.title,
                description=card_row.description,
                order=card_row.order,
                priority=card_row.priority,
                assignee_id=card_row.assignee_id,
                due_date=card_row.due_date,
                created_by=card_row.created_by,
                created_at=card_row.created_at,
                updated_at=card_row.updated_at,
                assignee=user_map.get(card_row.assignee_id),
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

    user_ids = {user_id for user_id in [card_row.assignee_id, card_row.created_by] if user_id}
    user_map = await get_user_map(session, user_ids)
    files_map = await get_card_files_map(session, [card_row.id])

    return CardDetailResponse(
        id=card_row.id,
        board_id=board_row.id,
        project_id=board_row.project_id,
        column_id=card_row.column_id,
        title=card_row.title,
        description=card_row.description,
        order=card_row.order,
        priority=card_row.priority,
        assignee_id=card_row.assignee_id,
        due_date=card_row.due_date,
        created_by=card_row.created_by,
        created_at=card_row.created_at,
        updated_at=card_row.updated_at,
        assignee=user_map.get(card_row.assignee_id),
        created_by_user=user_map.get(card_row.created_by),
        files=files_map.get(card_row.id, []),
    )


@router.get("/projects", response_model=ProjectListResponse, summary="Projectlar ro'yxati")
async def list_projects(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
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
            project_name=project_row.project_name,
            project_description=project_row.project_description,
            project_url=project_row.project_url,
            project_image=project_row.project_image,
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


@router.post("/projects", response_model=CreateResponse, summary="Yangi project yaratish")
async def create_project(
    project_name: str = Form(...),
    project_description: Optional[str] = Form(None),
    project_url: Optional[str] = Form(None),
    member_ids: Optional[List[str]] = Form(None),
    image: Optional[UploadFile] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    await ensure_projects_page_access(session, current_user)
    project_name = project_name.strip()
    if not project_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project nomi bo'sh bo'lishi mumkin emas",
        )

    parsed_member_ids = parse_member_ids_form(member_ids) or []
    validated_member_ids = await ensure_valid_member_ids(session, parsed_member_ids, current_user.id)
    image_path = await save_image(image, "project") if image else None

    result = await session.execute(
        insert(project)
        .values(
            project_name=project_name,
            project_description=project_description,
            project_url=project_url,
            project_image=image_path,
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
        project_name=project_row.project_name,
        project_description=project_row.project_description,
        project_url=project_row.project_url,
        project_image=project_row.project_image,
        created_by=project_row.created_by,
        created_at=project_row.created_at,
        updated_at=project_row.updated_at,
        created_by_user=user_map.get(project_row.created_by),
        members=members,
        boards=boards,
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
    member_ids: Optional[List[str]] = Form(None),
    image: Optional[UploadFile] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
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

    parsed_member_ids = parse_member_ids_form(member_ids)
    if parsed_member_ids is not None:
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


@router.delete("/projects/{project_id}", response_model=SuccessResponse, summary="Projectni o'chirish")
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
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

    delete_image_if_exists(project_row.project_image)
    await session.execute(delete(project).where(project.c.id == project_id))
    await session.commit()
    return SuccessResponse(message=f"Project '{project_row.project_name}' o'chirildi")


@router.get("/projects/{project_id}/boards", response_model=BoardListResponse, summary="Project boardlari")
async def list_project_boards(
    project_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
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
    title: str = Form(...),
    description: Optional[str] = Form(None),
    order: Optional[int] = Form(None),
    priority: str = Form("medium"),
    assignee_id: Optional[int] = Form(None),
    due_date: Optional[date] = Form(None),
    images: Optional[List[UploadFile]] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    column_row = await get_column_or_404(session, column_id)
    board_row = await get_board_or_404(session, column_row.board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)
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

    await ensure_user_exists(session, assignee_id)

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
            assignee_id=assignee_id,
            due_date=due_date,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        .returning(project_board_card.c.id)
    )
    card_id = result.scalar_one()

    await save_card_images(session, card_id, images)
    sibling_ids.insert(target_order, card_id)
    await resequence_cards(session, sibling_ids)
    await session.commit()

    return CreateResponse(message="Card muvaffaqiyatli yaratildi", id=card_id)


@router.get("/cards/{card_id}", response_model=CardDetailResponse, summary="Card detail")
async def get_card_detail(
    card_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    card_row = await get_card_or_404(session, card_id)
    column_row = await get_column_or_404(session, card_row.column_id)
    board_row = await get_board_or_404(session, column_row.board_id, include_archived=True)
    await ensure_project_member_access(session, board_row.project_id, current_user)
    return await build_card_detail(session, card_row)


@router.get("/open/projects/user/{user_id}", response_model=ProjectListResponse, summary="Ochiq user projectlari")
async def open_list_projects_by_user(
    user_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
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
            project_name=project_row.project_name,
            project_description=project_row.project_description,
            project_url=project_row.project_url,
            project_image=project_row.project_image,
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
        project_name=project_row.project_name,
        project_description=project_row.project_description,
        project_url=project_row.project_url,
        project_image=project_row.project_image,
        created_by=project_row.created_by,
        created_at=project_row.created_at,
        updated_at=project_row.updated_at,
        created_by_user=user_map.get(project_row.created_by),
        members=members,
        boards=boards,
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

    user_ids: Set[int] = set()
    for row in card_rows:
        if row.created_by:
            user_ids.add(row.created_by)
        if row.assignee_id:
            user_ids.add(row.assignee_id)
    user_map = await get_user_map(session, user_ids)
    files_map = await get_card_files_map(session, [row.id for row in card_rows])

    cards_payload = [
        CardListItemResponse(
            id=row.id,
            board_id=row.board_id,
            project_id=row.project_id,
            column_id=row.column_id,
            title=row.title,
            description=row.description,
            order=row.order,
            priority=row.priority,
            assignee_id=row.assignee_id,
            due_date=row.due_date,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
            assignee=user_map.get(row.assignee_id),
            created_by_user=user_map.get(row.created_by),
            files=files_map.get(row.id, []),
            project_name=row.project_name,
            board_name=row.board_name,
            column_name=row.column_name,
        )
        for row in card_rows
    ]

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
    await ensure_user_exists(session, user_id, "User topilmadi")

    card_row = await get_card_or_404(session, card_id)
    if not (
        card_row.assignee_id == user_id
        or (card_row.assignee_id is None and card_row.created_by == user_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bu userga tegishli card topilmadi")

    return await build_card_detail(session, card_row)


@router.patch("/cards/{card_id}", response_model=SuccessResponse, summary="Cardni yangilash")
async def update_card(
    card_id: int,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    priority: Optional[str] = Form(None),
    assignee_id: Optional[int] = Form(None),
    due_date: Optional[date] = Form(None),
    clear_existing_images: bool = Form(False),
    images: Optional[List[UploadFile]] = File(None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    card_row = await get_card_or_404(session, card_id)
    column_row = await get_column_or_404(session, card_row.column_id)
    board_row = await get_board_or_404(session, column_row.board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)

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
    if assignee_id is not None:
        update_values["assignee_id"] = assignee_id
    if due_date is not None:
        update_values["due_date"] = due_date

    if not update_values and not clear_existing_images and not images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangilanadigan ma'lumot topilmadi",
        )

    await ensure_user_exists(session, update_values.get("assignee_id"))
    if update_values:
        update_values["updated_at"] = datetime.utcnow()
        await session.execute(
            update(project_board_card).where(project_board_card.c.id == card_id).values(**update_values)
        )

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

    return SuccessResponse(message="Card muvaffaqiyatli yangilandi")


@router.patch("/cards/{card_id}/move", response_model=SuccessResponse, summary="Cardni ko'chirish")
async def move_card(
    card_id: int,
    move_data: CardMoveRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(get_current_active_user),
):
    card_row = await get_card_or_404(session, card_id)
    source_column = await get_column_or_404(session, card_row.column_id)
    source_board = await get_board_or_404(session, source_column.board_id)
    await ensure_project_member_access(session, source_board.project_id, current_user)

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
    card_row = await get_card_or_404(session, card_id)
    column_row = await get_column_or_404(session, card_row.column_id)
    board_row = await get_board_or_404(session, column_row.board_id)
    await ensure_project_member_access(session, board_row.project_id, current_user)

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
