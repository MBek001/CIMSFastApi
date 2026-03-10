import os
import re
from typing import Optional, List

import httpx


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def _normalize_notes(notes: str) -> str:
    return re.sub(r"\s+", " ", notes).strip()


def _fallback_summary(notes: str, max_len: int = 300) -> str:
    cleaned = _normalize_notes(notes)
    if not cleaned:
        return ""

    if len(cleaned) <= max_len:
        return cleaned

    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    summary = ""
    for sentence in sentences:
        candidate = (summary + " " + sentence).strip() if summary else sentence
        if len(candidate) > max_len:
            break
        summary = candidate

    if summary:
        return summary
    return cleaned[: max_len - 3].rstrip() + "..."


def _extract_response_text(payload: dict) -> Optional[str]:
    # Newer API responses may contain output_text directly.
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = payload.get("output")
    if not isinstance(output, list):
        return None

    chunks: list[str] = []
    for item in output:
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())

    if chunks:
        return " ".join(chunks).strip()
    return None


async def generate_customer_ai_summary(notes: Optional[str]) -> Optional[str]:
    if notes is None:
        return None

    cleaned_notes = _normalize_notes(notes)
    if not cleaned_notes:
        return None

    api_key = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
    model = os.getenv("OPENAI_MODEL", OPENAI_MODEL)
    base_url = os.getenv("OPENAI_BASE_URL", OPENAI_BASE_URL)

    if not api_key:
        return _fallback_summary(cleaned_notes)

    system_prompt = (
        "You are a CRM assistant. Summarize customer notes in Uzbek in 1-2 short sentences. "
        "Be factual, concise, and do not invent details."
    )

    payload = {
        "model": model,
        "temperature": 0.2,
        "max_output_tokens": 140,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": f"Notes:\n{cleaned_notes}"}],
            },
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/responses",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            summary = _extract_response_text(data)
            if summary:
                return summary[:500]
    except Exception:
        pass

    return _fallback_summary(cleaned_notes)


def _clip_text(value: str, max_len: int = 220) -> str:
    cleaned = _normalize_notes(value or "")
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


async def generate_update_tracking_ai_summary(
    full_name: str,
    month: int,
    year: int,
    update_percentage: float,
    working_days: int,
    update_days: int,
    missing_days: int,
    total_updates: int,
    valid_updates: int,
    invalid_updates: int,
    days_since_last: Optional[int],
    top_keywords: List[str],
    recent_updates: List[str],
    fallback_summary: str,
) -> str:
    fallback_text = _normalize_notes(fallback_summary or "")
    api_key = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
    model = os.getenv("OPENAI_MODEL", OPENAI_MODEL)
    base_url = os.getenv("OPENAI_BASE_URL", OPENAI_BASE_URL)

    if not api_key:
        return fallback_text

    keywords_text = ", ".join([kw for kw in top_keywords if kw]) if top_keywords else "yo'q"
    recent_lines = "\n".join(
        f"- {_clip_text(text)}" for text in recent_updates if _normalize_notes(text)
    ) or "- Oxirgi update matnlari topilmadi."

    days_since_last_text = "noma'lum" if days_since_last is None else str(days_since_last)

    system_prompt = (
        "Siz HR analytics assistant siz. Javobni faqat o'zbek tilida bering. "
        "Faqat berilgan ma'lumotlar asosida qisqa, aniq tahlil yozing. "
        "1) Baho 2) Asosiy kuzatuv 3) Risk 4) Aniq tavsiya formatida yozing."
    )

    user_prompt = (
        f"Xodim: {full_name}\n"
        f"Davr: {month}-{year}\n"
        f"Foiz: {update_percentage}\n"
        f"Ish kunlari: {working_days}\n"
        f"Update kunlari: {update_days}\n"
        f"Qolib ketgan kunlar: {missing_days}\n"
        f"Jami update: {total_updates}\n"
        f"Valid update: {valid_updates}\n"
        f"Invalid update: {invalid_updates}\n"
        f"Oxirgi updatedan beri kun: {days_since_last_text}\n"
        f"Asosiy so'zlar: {keywords_text}\n"
        f"Oxirgi update matnlari:\n{recent_lines}\n\n"
        "Yuqoridagilar asosida 6-8 qatorli qisqa professional xulosa bering."
    )

    payload = {
        "model": model,
        "temperature": 0.3,
        "max_output_tokens": 260,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/responses",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            summary = _extract_response_text(data)
            if summary:
                return _clip_text(summary, max_len=1300)
    except Exception:
        pass

    return fallback_text
