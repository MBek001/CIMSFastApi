import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from jose import jwt

from config import (
    GOOGLE_CALENDAR_EVENT_COLOR_ID,
    GOOGLE_CALENDAR_ID,
    GOOGLE_CALENDAR_SYNC_ENABLED,
    GOOGLE_CALENDAR_TIMEZONE,
    GOOGLE_SERVICE_ACCOUNT_FILE,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_SERVICE_ACCOUNT_SUBJECT,
)
from utils.recall_policy import get_effective_reminder_minutes, get_event_duration_minutes

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
EVENT_CUSTOMER_ID_KEY = "crmCustomerId"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    CALENDAR_TZ = ZoneInfo(GOOGLE_CALENDAR_TIMEZONE)
except Exception:
    CALENDAR_TZ = timezone(timedelta(hours=5), name=GOOGLE_CALENDAR_TIMEZONE)


def calendar_sync_enabled() -> bool:
    return GOOGLE_CALENDAR_SYNC_ENABLED


def _load_service_account_credentials() -> dict[str, Any]:
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        return json.loads(GOOGLE_SERVICE_ACCOUNT_JSON.replace("\\n", "\n"))

    if GOOGLE_SERVICE_ACCOUNT_FILE:
        raw_path = GOOGLE_SERVICE_ACCOUNT_FILE.strip()
        path_candidates = [
            Path(raw_path),
            Path.cwd() / raw_path,
            Path.cwd() / Path(raw_path).name,
            Path.cwd() / "secrets" / Path(raw_path).name,
            PROJECT_ROOT / raw_path,
            PROJECT_ROOT / Path(raw_path).name,
            PROJECT_ROOT / "secrets" / Path(raw_path).name,
        ]

        checked_paths: list[str] = []
        for candidate in path_candidates:
            candidate_str = str(candidate.resolve(strict=False))
            if candidate_str not in checked_paths:
                checked_paths.append(candidate_str)
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))

        raise RuntimeError(
            "Google service account credentials topilmadi. "
            f"GOOGLE_SERVICE_ACCOUNT_FILE={raw_path!r}, checked_paths={checked_paths}"
        )

    raise RuntimeError("Google service account credentials sozlanmagan")


async def _get_access_token() -> str:
    credentials = _load_service_account_credentials()
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {
        "iss": credentials["client_email"],
        "scope": GOOGLE_CALENDAR_SCOPE,
        "aud": GOOGLE_TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    if GOOGLE_SERVICE_ACCOUNT_SUBJECT:
        claims["sub"] = GOOGLE_SERVICE_ACCOUNT_SUBJECT

    headers = {}
    if credentials.get("private_key_id"):
        headers["kid"] = credentials["private_key_id"]

    assertion = jwt.encode(
        claims,
        credentials["private_key"],
        algorithm="RS256",
        headers=headers or None,
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        response.raise_for_status()
        payload = response.json()

    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Google access token olinmadi")
    return access_token


def _calendar_base_url() -> str:
    if not GOOGLE_CALENDAR_ID:
        raise RuntimeError("GOOGLE_CALENDAR_ID sozlanmagan")
    return f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(GOOGLE_CALENDAR_ID, safe='')}"


def _normalize_recall_time(recall_time: datetime, *, duration_minutes: int) -> tuple[datetime, datetime]:
    if recall_time.tzinfo is None:
        recall_time = recall_time.replace(tzinfo=timezone.utc)
    else:
        recall_time = recall_time.astimezone(timezone.utc)

    local_start = recall_time.astimezone(CALENDAR_TZ)
    local_end = local_start + timedelta(minutes=duration_minutes)
    return local_start, local_end


def _build_event_payload(customer_data: dict[str, Any]) -> dict[str, Any]:
    recall_time = customer_data.get("recall_time")
    if recall_time is None:
        raise RuntimeError("recall_time bo'lmasa Google Calendar event yaratilmaydi")

    status = customer_data.get("status")
    duration_minutes = get_event_duration_minutes(status)
    reminder_minutes = get_effective_reminder_minutes(status=status, recall_time=recall_time)
    start_at, end_at = _normalize_recall_time(recall_time, duration_minutes=duration_minutes)
    customer_id = customer_data["id"]
    customer_name = customer_data.get("full_name") or f"Customer #{customer_id}"

    description_lines = [
        f"CRM Customer ID: {customer_id}",
        f"Phone: {customer_data.get('phone_number') or '-'}",
        f"Platform: {customer_data.get('platform') or '-'}",
        f"Username: {customer_data.get('username') or '-'}",
        f"Assistant: {customer_data.get('assistant_name') or '-'}",
        f"Status: {status or '-'}",
        "",
        "Notes:",
        customer_data.get("notes") or "-",
    ]

    payload: dict[str, Any] = {
        "summary": f"Recall: {customer_name}",
        "description": "\n".join(description_lines),
        "start": {
            "dateTime": start_at.isoformat(),
            "timeZone": GOOGLE_CALENDAR_TIMEZONE,
        },
        "end": {
            "dateTime": end_at.isoformat(),
            "timeZone": GOOGLE_CALENDAR_TIMEZONE,
        },
        "extendedProperties": {
            "private": {
                EVENT_CUSTOMER_ID_KEY: str(customer_id),
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": reminder_minutes},
            ],
        },
    }
    if GOOGLE_CALENDAR_EVENT_COLOR_ID:
        payload["colorId"] = GOOGLE_CALENDAR_EVENT_COLOR_ID
    return payload


async def _find_event_id_by_customer_id(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    customer_id: int,
) -> Optional[str]:
    response = await client.get(
        f"{_calendar_base_url()}/events",
        headers=headers,
        params={
            "privateExtendedProperty": f"{EVENT_CUSTOMER_ID_KEY}={customer_id}",
            "maxResults": 1,
            "singleEvents": "true",
            "showDeleted": "false",
        },
    )
    response.raise_for_status()
    items = response.json().get("items", [])
    if not items:
        return None
    return items[0].get("id")


async def sync_customer_recall_event(customer_data: dict[str, Any]) -> None:
    if not calendar_sync_enabled():
        return

    access_token = await _get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    customer_id = int(customer_data["id"])

    async with httpx.AsyncClient(timeout=20.0) as client:
        event_id = await _find_event_id_by_customer_id(client, headers, customer_id)

        if customer_data.get("recall_time") is None:
            if event_id:
                response = await client.delete(
                    f"{_calendar_base_url()}/events/{quote(event_id, safe='')}",
                    headers=headers,
                    params={"sendUpdates": "none"},
                )
                response.raise_for_status()
            return

        payload = _build_event_payload(customer_data)
        if event_id:
            response = await client.patch(
                f"{_calendar_base_url()}/events/{quote(event_id, safe='')}",
                headers=headers,
                params={"sendUpdates": "none"},
                json=payload,
            )
        else:
            response = await client.post(
                f"{_calendar_base_url()}/events",
                headers=headers,
                params={"sendUpdates": "none"},
                json=payload,
            )
        response.raise_for_status()


async def delete_customer_recall_event(customer_id: int) -> None:
    if not calendar_sync_enabled():
        return

    access_token = await _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        event_id = await _find_event_id_by_customer_id(client, headers, customer_id)
        if not event_id:
            return
        response = await client.delete(
            f"{_calendar_base_url()}/events/{quote(event_id, safe='')}",
            headers=headers,
            params={"sendUpdates": "none"},
        )
        response.raise_for_status()
