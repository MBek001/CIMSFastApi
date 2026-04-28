from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_async_session

from cognilabsai.permissions import require_cognilabsai_chat, require_cognilabsai_integrations
from cognilabsai.realtime import manager
from cognilabsai.schemas import (
    ConversationItem,
    GenericMessageResponse,
    ImportConversationsRequest,
    ImportConversationsResponse,
    IntegrationConfigPayload,
    IntegrationConfigResponse,
    MessageItem,
    PauseConversationRequest,
    PauseUntilRequest,
    SendMessageRequest,
    TelegramSearchListResponse,
    TelegramSearchResult,
    TelegramStartConversationRequest,
)
from cognilabsai.service import (
    get_conversation,
    get_integration_config,
    get_messages,
    import_instagram_conversations,
    list_conversations,
    maybe_send_ai_reply,
    process_instagram_webhook_payload,
    send_operator_message,
    search_telegram_peer,
    search_telegram_peers,
    set_conversation_pause,
    update_integration_config,
    verify_websocket_api_key,
    start_telegram_outbound_conversation,
)


router = APIRouter(prefix="/cognilabsai", tags=["CognilabsAI"])
chat_router = APIRouter(prefix="/chat", tags=["CognilabsAI Chat"])
integrations_router = APIRouter(prefix="/integrations", tags=["CognilabsAI Integrations"])
webhook_router = APIRouter(prefix="/webhooks", tags=["CognilabsAI Webhooks"])


@chat_router.get("/conversations", response_model=list[ConversationItem])
async def chat_conversations(
    channel: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    return await list_conversations(session, channel=channel)


@chat_router.get("/conversations/{conversation_id}", response_model=ConversationItem)
async def chat_conversation_detail(
    conversation_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@chat_router.get("/conversations/{conversation_id}/messages", response_model=list[MessageItem])
async def chat_messages(
    conversation_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await get_messages(session, conversation_id, limit=limit, offset=offset)


@chat_router.post("/send-message")
async def chat_send_message(
    request: SendMessageRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    conversation = await get_conversation(session, request.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return await send_operator_message(session, request.conversation_id, request.text, current_user)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@chat_router.post("/pause", response_model=ConversationItem)
async def chat_pause(
    request: PauseConversationRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    conversation = await get_conversation(session, request.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    operator_name = " ".join(value for value in [getattr(current_user, "name", None), getattr(current_user, "surname", None)] if value) or getattr(current_user, "email", None)
    return await set_conversation_pause(
        session,
        conversation_id=request.conversation_id,
        ai_enabled=False,
        reason="manual",
        paused_until=None,
        operator_user_id=current_user.id,
        operator_name=operator_name,
        action="pause",
    )


@chat_router.post("/resume", response_model=ConversationItem)
async def chat_resume(
    request: PauseConversationRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    conversation = await get_conversation(session, request.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    operator_name = " ".join(value for value in [getattr(current_user, "name", None), getattr(current_user, "surname", None)] if value) or getattr(current_user, "email", None)
    return await set_conversation_pause(
        session,
        conversation_id=request.conversation_id,
        ai_enabled=True,
        reason=None,
        paused_until=None,
        operator_user_id=current_user.id,
        operator_name=operator_name,
        action="resume",
    )


@chat_router.post("/pause-until", response_model=ConversationItem)
async def chat_pause_until(
    request: PauseUntilRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    conversation = await get_conversation(session, request.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    operator_name = " ".join(value for value in [getattr(current_user, "name", None), getattr(current_user, "surname", None)] if value) or getattr(current_user, "email", None)
    return await set_conversation_pause(
        session,
        conversation_id=request.conversation_id,
        ai_enabled=False,
        reason="timed",
        paused_until=request.paused_until,
        operator_user_id=current_user.id,
        operator_name=operator_name,
        action="pause",
    )


@chat_router.post("/import-instagram-conversations", response_model=ImportConversationsResponse)
async def chat_import_instagram_conversations(
    request: ImportConversationsRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    return await import_instagram_conversations(session, request.folder_path)


@chat_router.post("/telegram/start")
async def chat_telegram_start(
    request: TelegramStartConversationRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    try:
        return await start_telegram_outbound_conversation(session, request.peer, request.text, current_user)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@chat_router.get("/telegram/search", response_model=TelegramSearchResult)
async def chat_telegram_search(
    query: str = Query(min_length=1),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    try:
        return await search_telegram_peer(session, query)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@chat_router.get("/telegram/search-list", response_model=TelegramSearchListResponse)
async def chat_telegram_search_list(
    query: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=20),
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    try:
        return await search_telegram_peers(session, query, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@chat_router.post("/conversations/{conversation_id}/retry-ai", response_model=GenericMessageResponse)
async def chat_retry_ai(
    conversation_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_chat),
):
    conversation = await get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await maybe_send_ai_reply(session, conversation_id)
    return GenericMessageResponse(message="AI processing requested")


@integrations_router.get("", response_model=IntegrationConfigResponse)
async def integrations_get(
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_integrations),
):
    return await get_integration_config(session)


@integrations_router.put("", response_model=IntegrationConfigResponse)
async def integrations_update(
    request: IntegrationConfigPayload,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cognilabsai_integrations),
):
    payload = request.model_dump(exclude_unset=True)
    return await update_integration_config(session, payload)


@webhook_router.get("/instagram")
async def instagram_webhook_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    session: AsyncSession = Depends(get_async_session),
):
    config = await get_integration_config(session)
    if hub_mode == "subscribe" and hub_verify_token == config.get("instagram_verify_token"):
        return PlainTextResponse(content=hub_challenge or "")
    raise HTTPException(status_code=403, detail="Invalid verify token")


@webhook_router.post("/instagram", response_model=GenericMessageResponse)
async def instagram_webhook_receive(
    payload: dict,
    session: AsyncSession = Depends(get_async_session),
):
    await process_instagram_webhook_payload(session, payload)
    return GenericMessageResponse(message="received")


@router.websocket("/ws/chat")
async def cognilabsai_websocket(
    websocket: WebSocket,
    api_key: str = Query(...),
    conversation_id: int | None = Query(default=None),
):
    from database import async_session_maker

    async with async_session_maker() as session:
        is_valid = await verify_websocket_api_key(session, api_key)
    if not is_valid:
        await websocket.close(code=1008)
        return
    await manager.connect(websocket, conversation_id=conversation_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, conversation_id=conversation_id)
    except Exception:
        await manager.disconnect(websocket, conversation_id=conversation_id)


router.include_router(chat_router)
router.include_router(integrations_router)
router.include_router(webhook_router)
