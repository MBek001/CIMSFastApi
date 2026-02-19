import os
import re
from typing import Optional

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

    if not OPENAI_API_KEY:
        return _fallback_summary(cleaned_notes)

    system_prompt = (
        "You are a CRM assistant. Summarize customer notes in Uzbek in 1-2 short sentences. "
        "Be factual, concise, and do not invent details."
    )

    payload = {
        "model": OPENAI_MODEL,
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
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{OPENAI_BASE_URL.rstrip('/')}/responses",
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
