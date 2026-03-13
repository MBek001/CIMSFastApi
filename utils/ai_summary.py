import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo

import httpx


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
FLEXIBLE_RECALL_OFFSET_MINUTES = 32
try:
    UZBEKISTAN_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    UZBEKISTAN_TZ = timezone(timedelta(hours=5), name="Asia/Tashkent")


def _debug_recall(message: str) -> None:
    print(f"[recall-debug] {message}", flush=True)


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


def _extract_first_json_object(text: str) -> Optional[dict]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _coerce_to_uz_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UZBEKISTAN_TZ)
    return value.astimezone(UZBEKISTAN_TZ)


def _parse_datetime_value(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        return _coerce_to_uz_datetime(datetime.fromisoformat(normalized))
    except Exception:
        pass

    patterns = (
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%d.%m.%Y %H:%M", False),
        ("%d.%m.%y %H:%M", False),
        ("%m/%d/%Y %I:%M %p", False),
        ("%m/%d/%Y %H:%M", False),
    )
    for pattern, _ in patterns:
        try:
            parsed = datetime.strptime(raw, pattern)
            return parsed.replace(tzinfo=UZBEKISTAN_TZ)
        except Exception:
            continue
    return None


def _extract_time_components(text: str) -> Optional[tuple[int, int]]:
    if not text:
        return None

    match = re.search(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)", text)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r"(?<!\d)([01]?\d|2[0-3])\s*(?:da|de|pm|am)?(?!\d)", text, re.IGNORECASE)
    if match:
        return int(match.group(1)), 0

    return None


def _contains_flexible_time_phrase(text: str) -> bool:
    normalized = _normalize_notes(text).lower()
    phrases = (
        "istalgan vaqt",
        "istalgan payt",
        "farqi yo'q qachon",
        "qachon xohlasangiz",
        "xohlagan vaqtda",
        "any time",
        "anytime",
        "whenever",
        "flexible time",
        "lyuboe vremya",
        "любой время",
        "любое время",
    )
    return any(phrase in normalized for phrase in phrases)


def _fallback_infer_recall_time(notes: str, base_time_uz: datetime) -> Optional[datetime]:
    text = _normalize_notes(notes).lower()
    if not text:
        _debug_recall("fallback: empty notes, returning None")
        return None

    absolute_match = re.search(
        r"(\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}(?::\d{2})?|\d{2}\.\d{2}\.\d{2,4}\s+\d{1,2}:\d{2}|\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}(?:\s?[ap]m)?)",
        text,
        re.IGNORECASE,
    )
    if absolute_match:
        parsed = _parse_datetime_value(absolute_match.group(1))
        if parsed:
            _debug_recall(
                f"fallback: absolute datetime matched '{absolute_match.group(1)}' -> {parsed.isoformat()}"
            )
            return parsed

    if any(token in text for token in ("hozir", "hazir", "now")):
        result = base_time_uz.replace(second=0, microsecond=0)
        _debug_recall(f"fallback: immediate time phrase matched -> {result.isoformat()}")
        return result

    relative_match = re.search(
        r"(?:(\d+)|bir|one)\s*(soat|hour|kun|day|minute|minut|min|daqiqa)[a-z]*\s*(?:keyin|later)",
        text,
        re.IGNORECASE,
    )
    if relative_match:
        raw_amount = relative_match.group(1)
        amount = int(raw_amount) if raw_amount else 1
        unit = relative_match.group(2).lower()
        if unit in {"soat", "hour"}:
            result = (base_time_uz + timedelta(hours=amount)).replace(second=0, microsecond=0)
            _debug_recall(f"fallback: relative hour matched amount={amount} -> {result.isoformat()}")
            return result
        if unit in {"kun", "day"}:
            result = (base_time_uz + timedelta(days=amount)).replace(second=0, microsecond=0)
            _debug_recall(f"fallback: relative day matched amount={amount} -> {result.isoformat()}")
            return result
        result = (base_time_uz + timedelta(minutes=amount)).replace(second=0, microsecond=0)
        _debug_recall(f"fallback: relative minute matched amount={amount} -> {result.isoformat()}")
        return result

    if _contains_flexible_time_phrase(text):
        result = (base_time_uz + timedelta(minutes=FLEXIBLE_RECALL_OFFSET_MINUTES)).replace(
            second=0,
            microsecond=0,
        )
        _debug_recall(f"fallback: flexible time matched -> {result.isoformat()}")
        return result

    time_parts = _extract_time_components(text)
    if any(token in text for token in ("ertaga", "zavtra", "tomorrow")) and time_parts:
        hours, minutes = time_parts
        result = (base_time_uz + timedelta(days=1)).replace(
            hour=hours,
            minute=minutes,
            second=0,
            microsecond=0,
        )
        _debug_recall(f"fallback: tomorrow phrase matched -> {result.isoformat()}")
        return result

    if any(token in text for token in ("bugun", "segodnya", "today")) and time_parts:
        hours, minutes = time_parts
        result = base_time_uz.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        _debug_recall(f"fallback: today phrase matched -> {result.isoformat()}")
        return result

    if "preferred call time" in text and time_parts:
        hours, minutes = time_parts
        candidate = base_time_uz.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if candidate < base_time_uz:
            candidate += timedelta(days=1)
        _debug_recall(f"fallback: preferred call time with clock matched -> {candidate.isoformat()}")
        return candidate

    if time_parts and not re.search(r"\b\d{1,2}\s*(?:-|dan|to)\s*\d{1,2}\b", text):
        hours, minutes = time_parts
        candidate = base_time_uz.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if candidate < base_time_uz:
            candidate += timedelta(days=1)
        _debug_recall(f"fallback: generic clock matched -> {candidate.isoformat()}")
        return candidate

    _debug_recall("fallback: no rule matched, returning None")
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


async def infer_recall_time_from_notes_ai(
    notes: Optional[str],
    created_at: Optional[datetime] = None,
) -> Optional[datetime]:
    if notes is None:
        _debug_recall("ai: notes is None, returning None")
        return None

    cleaned_notes = _normalize_notes(notes)
    if not cleaned_notes:
        _debug_recall("ai: notes empty after normalize, returning None")
        return None

    base_time_uz = _coerce_to_uz_datetime(created_at or datetime.now(UZBEKISTAN_TZ))
    api_key = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
    model = os.getenv("OPENAI_MODEL", OPENAI_MODEL)
    base_url = os.getenv("OPENAI_BASE_URL", OPENAI_BASE_URL)

    _debug_recall(
        f"ai: start parse base_time={base_time_uz.isoformat()} notes='{cleaned_notes[:220]}'"
    )

    if not api_key:
        _debug_recall("ai: OPENAI_API_KEY missing, switching to fallback")
        return _fallback_infer_recall_time(cleaned_notes, base_time_uz)

    system_prompt = (
        "You extract an exact recall datetime for a CRM lead. "
        "Return only a JSON object with keys recall_time, confidence, reason. "
        "recall_time must be either null or an ISO-8601 datetime with Asia/Tashkent offset +05:00. "
        "Use the provided created_at as the base time for relative phrases. "
        "If the note says flexible timing such as 'istalgan vaqt' or 'any time', set recall_time to created_at plus 32 minutes. "
        "If the note is ambiguous, contains only a time range, or does not specify one exact recall moment, return null. "
        "Examples: "
        "'ertaga soat 10 da' => next day 10:00, "
        "'bir soatdan keyin' => created_at plus 1 hour, "
        "'bugun 21:20' => same date 21:20, "
        "'21:20' => same date 21:20 if still upcoming, otherwise next day 21:20, "
        "'hozir' => created_at rounded to minute, "
        "'istalgan vaqt' => created_at plus 32 minutes, "
        "'9:00 to 18:00' => null."
    )

    user_prompt = (
        f"created_at_uz: {base_time_uz.isoformat()}\n"
        f"notes: {cleaned_notes}\n\n"
        "Return JSON only."
    )

    payload = {
        "model": model,
        "temperature": 0,
        "max_output_tokens": 160,
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
        async with httpx.AsyncClient(timeout=20.0) as client:
            _debug_recall(f"ai: sending request to model={model} base_url={base_url}")
            response = await client.post(
                f"{base_url.rstrip('/')}/responses",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            raw_text = _extract_response_text(data)
            _debug_recall(f"ai: raw response text='{(raw_text or '')[:220]}'")
            if raw_text:
                parsed_json = _extract_first_json_object(raw_text)
                if parsed_json:
                    _debug_recall(f"ai: parsed json={parsed_json}")
                    recall_time_value = parsed_json.get("recall_time")
                    if recall_time_value in (None, "", "null"):
                        if _contains_flexible_time_phrase(cleaned_notes):
                            result = (
                                base_time_uz + timedelta(minutes=FLEXIBLE_RECALL_OFFSET_MINUTES)
                            ).replace(second=0, microsecond=0)
                            _debug_recall(
                                f"ai: model returned null but flexible phrase matched -> {result.isoformat()}"
                            )
                            return result
                        _debug_recall("ai: model returned null recall_time")
                        return None
                    parsed_dt = _parse_datetime_value(str(recall_time_value))
                    if parsed_dt:
                        result = parsed_dt.replace(second=0, microsecond=0)
                        _debug_recall(f"ai: parsed model recall_time -> {result.isoformat()}")
                        return result
                else:
                    _debug_recall("ai: response text did not contain valid JSON object")
            else:
                _debug_recall("ai: response text empty")
    except Exception as exc:
        _debug_recall(f"ai: request failed, switching to fallback, error={exc}")

    return _fallback_infer_recall_time(cleaned_notes, base_time_uz)


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
