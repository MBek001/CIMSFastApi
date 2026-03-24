from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_active_user
from database import get_async_session
from schemes.schemes_ai import CimsAiChatRequest, CimsAiChatResponse
from utils.cims_ai import build_cims_ai_context, generate_cims_ai_answer


router = APIRouter(prefix="/ai", tags=["CIMS AI"])


def require_cims_ai_access(current_user=Depends(get_current_active_user)):
    role = getattr(current_user, "role", None)
    role_name = str(getattr(role, "name", "") or "").strip().lower()
    role_value = str(getattr(role, "value", "") or "").strip().lower()
    role_plain = str(role or "").strip().lower()
    company_code = str(getattr(current_user, "company_code", "") or "").strip().lower()

    if not (
        role_name == "ceo"
        or role_value == "ceo"
        or role_plain == "ceo"
        or company_code == "ceo"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CIMS AI agent faqat CEO uchun ochiq"
        )
    return current_user


@router.post("/chat", response_model=CimsAiChatResponse, summary="CIMS AI analytics chat")
async def cims_ai_chat(
    payload: CimsAiChatRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user=Depends(require_cims_ai_access),
):
    chat_history = [item.model_dump() for item in payload.history]
    context = await build_cims_ai_context(session, payload.question, chat_history)
    answer, used_llm = await generate_cims_ai_answer(
        session=session,
        question=payload.question,
        context=context,
        history=chat_history,
    )

    return CimsAiChatResponse(
        answer=answer,
        used_llm=used_llm,
        intents=context.get("intents", []),
        period=context.get("period", {}),
        employee=context.get("employee"),
        context=context,
    )
