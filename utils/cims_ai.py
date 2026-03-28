import json
import os
import re
from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import Date, and_, cast, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from models.admin_models import (
    CardType,
    CustomerStatus,
    CurrencyType,
    CustomerType,
    FinanceType,
    TransactionStatus,
    company_recurring_payment,
    customer,
    customer_status_change_log,
    daily_update_log,
    exchange_rate,
    finance,
    sales_manager_assignment,
    workday_override,
)
from models.projects_models import project, project_board, project_board_card, project_board_column, project_member
from models.user_models import (
    UserRole,
    attendance_log,
    compensation_bonus,
    compensation_mistake,
    monthly_update,
    user,
    user_payment,
)
from utils.crypto import decrypt_text
from utils.workday_overrides import fetch_override_pack, list_expected_update_days, summarize_expected_days

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
try:
    UZ_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    UZ_TZ = timezone(timedelta(hours=5))

MONTH_ALIASES = {
    1: {"1", "01", "janvar", "yanvar", "january", "jan"},
    2: {"2", "02", "fevral", "february", "feb"},
    3: {"3", "03", "mart", "march", "mar"},
    4: {"4", "04", "aprel", "april", "apr"},
    5: {"5", "05", "may"},
    6: {"6", "06", "iyun", "june", "jun"},
    7: {"7", "07", "iyul", "july", "jul"},
    8: {"8", "08", "avgust", "august", "aug"},
    9: {"9", "09", "sentabr", "september", "sep"},
    10: {"10", "oktabr", "october", "oct"},
    11: {"11", "noyabr", "november", "nov"},
    12: {"12", "dekabr", "december", "dec"},
}
MONTH_NAMES_UZ = {
    1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel", 5: "May", 6: "Iyun",
    7: "Iyul", 8: "Avgust", 9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr",
}
SQL_ANALYTICS_TABLES: dict[str, list[str]] = {
    "user": ["id", "email", "name", "surname", "company_code", "role", "role_name", "job_title", "is_active"],
    "monthly_update": ["id", "user_id", "year", "month", "update_date", "update_percentage", "salary_amount", "next_payment_date", "note"],
    "daily_update_log": ["id", "user_id", "telegram_username", "update_date", "update_content", "is_valid", "created_at"],
    "customer": ["id", "platform", "username", "status", "status_name", "type", "assistant_name", "notes", "aisummary", "recall_time", "created_at"],
    "sales_manager_assignment": ["id", "customer_id", "sales_manager_id", "assigned_at", "assigned_by", "is_active"],
    "customer_status_change_log": ["id", "customer_id", "from_status", "to_status", "changed_at"],
    "finance": ["id", "type", "status", "card", "service", "summ", "currency", "date", "donation", "donation_percentage", "tax_percentage", "exchange_rate", "transaction_status", "initial_date"],
    "exchange_rate": ["id", "usd_to_uzs", "updated_at"],
    "user_payment": ["id", "project", "date", "summ", "payment"],
    "company_recurring_payment": ["id", "title", "amount", "payment_day", "payment_time", "note", "is_active", "created_at", "updated_at"],
    "workday_override": ["id", "special_date", "target_type", "target_key", "user_id", "day_type", "title", "note", "workday_hours", "update_required", "created_by", "created_at", "updated_at"],
    "attendance_log": ["id", "employee_id", "attendance_date", "check_in_time", "check_out_time", "created_by", "created_at", "updated_at"],
    "compensation_mistake": ["id", "employee_id", "reviewer_id", "project_id", "category", "severity", "title", "incident_date", "reached_client", "unclear_task", "created_by", "created_at", "updated_at"],
    "compensation_bonus": ["id", "employee_id", "project_id", "bonus_type", "title", "award_date", "created_by", "created_at", "updated_at"],
    "project": ["id", "project_name", "project_description", "project_url", "project_image", "created_by", "created_at", "updated_at"],
    "project_member": ["id", "project_id", "user_id", "created_at"],
    "project_board": ["id", "project_id", "name", "description", "created_by", "created_at", "is_archived"],
    "project_board_column": ["id", "board_id", "name", "order", "color", "created_at"],
    "project_board_card": ["id", "column_id", "title", "description", "order", "priority", "assignee_id", "due_date", "created_by", "created_at", "updated_at"],
}

SALES_NOTE_STOPWORDS = {
    "mijoz", "lead", "customer", "client", "crm", "aloqa", "boglandi", "bog'landi",
    "yozildi", "gaplashildi", "telefon", "tel", "note", "izoh", "status", "qayta",
    "call", "manager", "sales", "savdo", "sotuv", "clint", "bilan", "uchun", "ham",
    "yana", "lekin", "yoki", "bor", "yoq", "va", "bu", "shu", "bugun", "kecha",
    "erta", "task", "the", "and", "for", "with", "from", "that", "this",
}


@dataclass
class PeriodSpec:
    label: str
    start_date: date
    end_date: date
    kind: str
    month: Optional[int] = None
    year: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "month": self.month,
            "year": self.year,
        }


def _n(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _clip(text: str, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= max_len else text[: max_len - 3].rstrip() + "..."


def _enum(value: Any) -> Any:
    return getattr(value, "value", value)


def _money(value: Any) -> float:
    return round(float(value or 0), 2)


def _schema_brief() -> str:
    return "\n".join(
        f"- {table}: {', '.join(columns)}"
        for table, columns in SQL_ANALYTICS_TABLES.items()
    )


def _extract_json_object(text_value: str) -> Optional[dict[str, Any]]:
    raw = (text_value or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _is_safe_select_sql(sql: str) -> bool:
    query = (sql or "").strip()
    if not query or len(query) > 5000:
        return False
    normalized = _n(query)
    if ";" in query:
        return False
    if not (normalized.startswith("select ") or normalized.startswith("with ")):
        return False
    if re.search(r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|comment|copy|call|execute|merge|vacuum|analyze)\b", normalized):
        return False
    used_tables = {
        item
        for item in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", normalized)
    }
    return used_tables.issubset(set(SQL_ANALYTICS_TABLES.keys()))


def _history_text(history: Optional[list[dict[str, str]]], *, roles: tuple[str, ...] = ("user",), limit: int = 4) -> str:
    if not history:
        return ""
    items = [
        (item.get("content") or "").strip()
        for item in history
        if (item.get("role") or "").strip() in roles and (item.get("content") or "").strip()
    ]
    return "\n".join(items[-limit:])


def _has_explicit_period(question: str) -> bool:
    q = _n(question)
    if re.search(r"\b(20\d{2})\b", q):
        return True
    for aliases in MONTH_ALIASES.values():
        if any(re.search(rf"\b{re.escape(alias)}\b", q) for alias in aliases):
            return True
    return any(
        marker in q
        for marker in [
            "o'tgan oy", "otgan oy", "last month",
            "shu oy", "joriy oy", "this month",
            "o'tgan hafta", "otgan hafta", "last week",
            "shu hafta", "joriy hafta", "this week",
            "kecha", "yesterday", "bugun", "today", "oxirgi", "last ",
        ]
    )


def _is_follow_up_question(question: str) -> bool:
    q = _n(question)
    short = len(q.split()) <= 8
    pronouns = ["u ", "unda", "o'sha", "osha", "shu", "chi", "qancha edi", "nechi edi", "anaqa", "qaysi biri"]
    return short or any(token in q for token in pronouns)


def _next_company_payment_occurrence(payment_day: int, payment_time: Any, base_day: date) -> datetime:
    year = base_day.year
    month = base_day.month
    while True:
        last_day = monthrange(year, month)[1]
        target_day = min(max(int(payment_day or 1), 1), last_day)
        target_dt = datetime(year, month, target_day, payment_time.hour, payment_time.minute, payment_time.second)
        if target_dt.date() >= base_day:
            return target_dt
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _should_run_sql_analytics(question: str, context: dict[str, Any]) -> bool:
    q = _n(question)
    explicit_markers = [
        "sql", "query", "jadval", "table", "rows", "row", "ro'yxat", "royxat", "list",
        "eng", "top", "qaysi", "kim", "kimlar", "taqqosla", "solishtir", "trend",
        "daily", "kunma-kun", "oyma-oy", "haftama-hafta", "group by", "filter",
    ]
    if any(marker in q for marker in explicit_markers):
        return True
    populated_sections = [
        key
        for key in [
            "employee_update",
            "lead_stats",
            "customer_detail",
            "finance_summary",
            "payment_summary",
            "recall_summary",
            "sales_manager_stats",
            "project_overview",
            "company_overview",
            "data_hub",
        ]
        if context.get(key)
    ]
    return not populated_sections


def _extract_response_text(payload: dict) -> Optional[str]:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for item in output:
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"} and isinstance(part.get("text"), str):
                parts.append(part["text"].strip())
    return " ".join([p for p in parts if p]).strip() or None


def _month_range(year: int, month: int) -> tuple[date, date]:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return date(year, month, 1), next_month - timedelta(days=1)


def _resolve_period(question: str) -> PeriodSpec:
    q, today = _n(question), date.today()
    year_match = re.search(r"\b(20\d{2})\b", q)
    year = int(year_match.group(1)) if year_match else today.year
    for month_num, aliases in MONTH_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", q) for alias in aliases):
            start, end = _month_range(year, month_num)
            return PeriodSpec(f"{MONTH_NAMES_UZ[month_num]} {year}", start, min(end, today), "month", month_num, year)
    if "o'tgan oy" in q or "otgan oy" in q or "last month" in q:
        month = today.month - 1 or 12
        year = today.year - 1 if today.month == 1 else today.year
        start, end = _month_range(year, month)
        return PeriodSpec(f"O'tgan oy ({MONTH_NAMES_UZ[month]} {year})", start, end, "month", month, year)
    if "shu oy" in q or "joriy oy" in q or "this month" in q:
        start, end = _month_range(today.year, today.month)
        return PeriodSpec(f"Shu oy ({MONTH_NAMES_UZ[today.month]} {today.year})", start, min(end, today), "month", today.month, today.year)
    if "o'tgan hafta" in q or "otgan hafta" in q or "last week" in q:
        start = today - timedelta(days=today.weekday() + 7)
        return PeriodSpec("O'tgan hafta", start, start + timedelta(days=6), "week")
    if "shu hafta" in q or "joriy hafta" in q or "this week" in q:
        start = today - timedelta(days=today.weekday())
        return PeriodSpec("Shu hafta", start, min(start + timedelta(days=6), today), "week")
    if "kecha" in q or "yesterday" in q:
        d = today - timedelta(days=1)
        return PeriodSpec("Kecha", d, d, "day")
    if "bugun" in q or "today" in q:
        return PeriodSpec("Bugun", today, today, "day")
    m = re.search(r"(oxirgi|last)\s+(\d+)\s+(kun|day)", q)
    if m:
        days = max(int(m.group(2)), 1)
        return PeriodSpec(f"Oxirgi {days} kun", today - timedelta(days=days - 1), today, "days")
    start, end = _month_range(today.year, today.month)
    return PeriodSpec(f"Shu oy ({MONTH_NAMES_UZ[today.month]} {today.year})", start, min(end, today), "month", today.month, today.year)


async def _match_user(session: AsyncSession, question: str, role: Optional[UserRole] = None) -> Optional[dict[str, Any]]:
    q = _n(question)
    query = select(user.c.id, user.c.name, user.c.surname, user.c.email, user.c.role, user.c.is_active).order_by(user.c.name, user.c.surname)
    if role is not None:
        query = query.where(user.c.role == role)
    rows = (await session.execute(query)).fetchall()
    best, best_score = None, 0
    for row in rows:
        aliases = {_n(f"{row.name} {row.surname}"), _n(row.name or ""), _n(row.surname or ""), _n(row.email or "")}
        for alias in aliases:
            if alias and alias in q and len(alias) > best_score:
                best_score = len(alias)
                best = {
                    "id": row.id,
                    "name": row.name,
                    "surname": row.surname,
                    "full_name": f"{row.name} {row.surname}".strip(),
                    "email": row.email,
                    "role": _enum(row.role),
                    "is_active": bool(row.is_active),
                }
    return best


async def _match_customer(session: AsyncSession, question: str) -> Optional[dict[str, Any]]:
    q = _n(question)
    if not any(word in q for word in ["mijoz", "lead", "customer", "client", "telefon", "username", "@"]):
        return None
    rows = (await session.execute(
        select(
            customer.c.id, customer.c.full_name, customer.c.phone_number, customer.c.platform, customer.c.username,
            customer.c.status, customer.c.status_name, customer.c.type, customer.c.assistant_name, customer.c.notes,
            customer.c.aisummary, customer.c.recall_time, customer.c.created_at
        ).order_by(desc(customer.c.created_at)).limit(2000)
    )).fetchall()
    best, best_score = None, 0
    for row in rows:
        full_name, phone = decrypt_text(row.full_name), decrypt_text(row.phone_number)
        aliases = {_n(full_name or ""), _n(phone or ""), _n(row.username or ""), _n(row.platform or "")}
        for alias in aliases:
            if alias and alias in q and len(alias) > best_score:
                best_score = len(alias)
                best = {
                    "id": row.id,
                    "full_name": full_name,
                    "phone_number": phone,
                    "platform": row.platform,
                    "username": row.username,
                    "status": row.status_name or _enum(row.status),
                    "type": _enum(row.type),
                    "assistant_name": row.assistant_name,
                    "notes": row.notes,
                    "aisummary": row.aisummary,
                    "recall_time": row.recall_time.isoformat() if row.recall_time else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
    return best


def _detect_customer_type(question: str) -> Optional[str]:
    q = _n(question)
    if "international" in q or "xalqaro" in q:
        return "international"
    if "local" in q or "mahalliy" in q:
        return "local"
    return None


def _detect_actions(question: str, employee: Optional[dict[str, Any]], sales_manager: Optional[dict[str, Any]], customer_match: Optional[dict[str, Any]]) -> list[str]:
    q = _n(question)
    actions: list[str] = []
    if employee and any(x in q for x in ["update", "foiz", "hisobot", "performance", "statistika", "oylik"]):
        actions.append("employee_update")
    elif employee and not any(x in q for x in ["lead", "mijoz", "customer", "finance", "balans", "project"]):
        actions.append("employee_update")
    if any(x in q for x in ["lead", "mijoz", "customer", "crm", "status", "konversiya", "kelgan"]):
        actions.append("lead_stats")
    if any(x in q for x in ["savdo", "sotuv", "sales advice", "maslahat", "tavsiya", "conversion", "yaxshilash"]):
        actions.append("lead_stats")
    if customer_match:
        actions.append("customer_detail")
    if any(x in q for x in ["finance", "balans", "balance", "kirim", "chiqim", "karta", "card", "donation", "uzs", "usd", "pul"]):
        actions.append("finance_summary")
    if any(x in q for x in ["payment", "to'lov", "tolov", "due", "qarzdor", "reminder", "oylik to'lov"]):
        actions.append("payment_summary")
    if any(x in q for x in ["recall", "call", "qayta aloqa", "eslatma", "need_to_call", "bog'lan", "boglan"]):
        actions.append("recall_summary")
    if sales_manager and any(x in q for x in ["manager", "sales", "assign", "biriktirilgan", "konversiya", "status"]):
        actions.append("sales_manager_stats")
    if any(x in q for x in ["project", "loyiha", "board", "kanban", "task", "card"]):
        actions.append("project_overview")
    if any(x in q for x in ["company", "kompaniya", "umumiy", "overview", "dashboard", "xulosa"]) or not actions:
        actions.append("company_overview")
    return list(dict.fromkeys(actions))


def _extract_note_topics(texts: list[str], limit: int = 8) -> list[dict[str, Any]]:
    words: list[str] = []
    for text in texts:
        if not text:
            continue
        tokens = re.findall(r"[a-zA-Z0-9']+", text.lower())
        for token in tokens:
            if token.isdigit() or len(token) < 3 or token in SALES_NOTE_STOPWORDS:
                continue
            words.append(token)
    counts = Counter(words)
    return [{"keyword": word, "count": count} for word, count in counts.most_common(limit)]


def _build_note_signal_breakdown(texts: list[str]) -> dict[str, int]:
    joined = " \n ".join(_n(text) for text in texts if text)
    signal_keywords = {
        "price_objection": ["narx", "qimmat", "budjet", "budget", "summa", "to'lov", "tolov"],
        "timing_delay": ["keyin", "kechroq", "vaqt", "busy", "band", "haftadan", "oydan"],
        "trust_objection": ["ishonch", "garantiya", "portfolio", "case", "tajriba", "review"],
        "feature_gap": ["funksiya", "feature", "integratsiya", "integration", "api", "bot", "crm", "sayt"],
        "follow_up_needed": ["qayta", "eslat", "call", "aloqa", "bog'lan", "boglan", "yozish", "javob"],
    }
    return {
        key: sum(joined.count(keyword) for keyword in keywords)
        for key, keywords in signal_keywords.items()
    }


def _build_sales_recommendations(
    *,
    total_leads: int,
    status_breakdown: list[dict[str, Any]],
    note_signal_breakdown: dict[str, int],
    notes_count: int,
) -> list[str]:
    recommendations: list[str] = []
    status_map = {_n(str(item.get("status") or "")): int(item.get("count") or 0) for item in status_breakdown}
    need_to_call = status_map.get("need_to_call", 0)
    contacted = status_map.get("contacted", 0)
    project_started = status_map.get("project_started", 0)
    rejected = status_map.get("rejected", 0)

    if need_to_call > 0:
        recommendations.append(f"`need_to_call` dagi {need_to_call} ta lead bilan qayta aloqa qilishni birinchi prioritet qiling.")
    if total_leads > 0 and project_started == 0:
        recommendations.append("Lead kelmoqda, lekin `project_started` yo'q. Birinchi call skripti va offerni qayta ko'rib chiqing.")
    elif contacted > 0 and project_started / max(contacted, 1) < 0.35:
        recommendations.append("`contacted` dan `project_started` ga o'tish past. Demo, case study va aniq taklifni kuchaytiring.")
    if rejected > max(total_leads // 4, 2):
        recommendations.append("`rejected` soni yuqori. Lead qualification va birinchi suhbatdagi ehtiyoj aniqlashni yaxshilang.")
    if note_signal_breakdown.get("price_objection", 0) > 0:
        recommendations.append("Note larda narx/budjet e'tirozi ko'p. Paketlar yoki bosqichma-bosqich to'lov variantini tayyorlang.")
    if note_signal_breakdown.get("timing_delay", 0) > 0:
        recommendations.append("Ko'p lead keyinroq qaytishini yozgan. Qayta follow-up sanasini qat'iy yuriting.")
    if note_signal_breakdown.get("trust_objection", 0) > 0:
        recommendations.append("Ishonchni oshirish uchun portfolio, case va natija misollarini birinchi suhbatdayoq yuboring.")
    if note_signal_breakdown.get("feature_gap", 0) > 0:
        recommendations.append("Funksiya va integratsiya savollari ko'p. Tayyor capability list va FAQ ishlating.")
    if note_signal_breakdown.get("follow_up_needed", 0) > 0 and notes_count > 0:
        recommendations.append("Har bir lead uchun note ichida keyingi aniq qadam va sana bo'lishi kerak.")
    return recommendations[:5]


async def _employee_update_context(session: AsyncSession, employee: dict[str, Any], period: PeriodSpec) -> dict[str, Any]:
    override_pack = await fetch_override_pack(session, period.start_date, period.end_date, user_ids=[employee["id"]])
    month_summary = summarize_expected_days(override_pack, employee["id"], period.start_date, period.end_date)
    rows = (await session.execute(
        select(daily_update_log.c.update_date, daily_update_log.c.update_content, daily_update_log.c.created_at)
        .where(and_(daily_update_log.c.user_id == employee["id"], daily_update_log.c.update_date >= period.start_date, daily_update_log.c.update_date <= period.end_date, daily_update_log.c.is_valid == True))
        .order_by(daily_update_log.c.update_date.asc(), daily_update_log.c.created_at.asc())
    )).fetchall()
    submitted = {row.update_date for row in rows}
    expected = list_expected_update_days(override_pack, employee["id"], period.start_date, period.end_date)
    if date.today() in expected and date.today() not in submitted:
        expected = [item for item in expected if item != date.today()]
    working_days, update_days = len(expected), len(submitted & set(expected))
    calc_pct = round((update_days / working_days) * 100, 1) if working_days else 0.0
    report_summary, report_items = None, []
    if period.month and period.year:
        aliases = {item.lower() for item in MONTH_ALIASES.get(period.month, set())}
        report_rows = (await session.execute(
            select(monthly_update.c.id, monthly_update.c.update_percentage, monthly_update.c.salary_amount, monthly_update.c.update_date, monthly_update.c.note, monthly_update.c.next_payment_date)
            .where(and_(monthly_update.c.user_id == employee["id"], monthly_update.c.year == period.year, func.lower(func.trim(monthly_update.c.month)).in_(aliases)))
            .order_by(monthly_update.c.update_date.desc(), monthly_update.c.id.desc())
        )).fetchall()
        if report_rows:
            percentages = [float(item.update_percentage or 0) for item in report_rows]
            report_summary = {
                "reports_count": len(report_rows),
                "average_update_percentage": round(sum(percentages) / len(percentages), 2),
                "min_update_percentage": round(min(percentages), 2),
                "max_update_percentage": round(max(percentages), 2),
                "latest_report_date": report_rows[0].update_date.isoformat() if report_rows[0].update_date else None,
                "total_salary_amount": round(sum(float(item.salary_amount or 0) for item in report_rows), 2),
            }
            report_items = [{"id": item.id, "update_percentage": float(item.update_percentage or 0), "salary_amount": float(item.salary_amount or 0), "update_date": item.update_date.isoformat() if item.update_date else None, "next_payment_date": item.next_payment_date.isoformat() if item.next_payment_date else None, "note": _clip(item.note or "", 180) if item.note else None} for item in report_rows[:5]]
    latest = rows[-1] if rows else None
    return {
        "employee": employee,
        "period": period.as_dict(),
        "working_days": working_days,
        "update_days": update_days,
        "calculated_update_percentage": calc_pct,
        "total_valid_updates": len(rows),
        "day_off_count": month_summary.get("day_off_count", 0),
        "short_day_count": month_summary.get("short_day_count", 0),
        "latest_update_date": latest.update_date.isoformat() if latest else None,
        "latest_update_preview": _clip(latest.update_content or "", 220) if latest else None,
        "recent_updates": [{"date": item.update_date.isoformat(), "content": _clip(item.update_content or "", 220)} for item in sorted(rows, key=lambda x: (x.update_date, x.created_at or datetime.min), reverse=True)[:5]],
        "monthly_report_summary": report_summary,
        "monthly_report_items": report_items,
    }


async def _lead_stats_context(session: AsyncSession, period: PeriodSpec, customer_type: Optional[str]) -> dict[str, Any]:
    filters = [cast(customer.c.created_at, Date) >= period.start_date, cast(customer.c.created_at, Date) <= period.end_date]
    if customer_type == "international":
        filters.append(customer.c.type == CustomerType.international)
    elif customer_type == "local":
        filters.append(or_(customer.c.type == CustomerType.local, customer.c.type == None))
    total = (await session.execute(select(func.count(customer.c.id)).where(and_(*filters)))).scalar() or 0
    platform_rows = (await session.execute(select(customer.c.platform, func.count(customer.c.id).label("count")).where(and_(*filters)).group_by(customer.c.platform).order_by(desc("count")).limit(10))).fetchall()
    status_rows = (await session.execute(select(customer.c.status_name, customer.c.status, func.count(customer.c.id).label("count")).where(and_(*filters)).group_by(customer.c.status_name, customer.c.status).order_by(desc("count")))).fetchall()
    type_rows = (await session.execute(select(customer.c.type, func.count(customer.c.id).label("count")).where(and_(cast(customer.c.created_at, Date) >= period.start_date, cast(customer.c.created_at, Date) <= period.end_date)).group_by(customer.c.type))).fetchall()
    local_count, intl_count = 0, 0
    for row in type_rows:
        if row.type == CustomerType.international:
            intl_count += row.count
        else:
            local_count += row.count
    note_rows = (await session.execute(
        select(
            customer.c.id,
            customer.c.notes,
            customer.c.status_name,
            customer.c.status,
            customer.c.platform,
            customer.c.assistant_name,
            customer.c.created_at,
        )
        .where(and_(*filters))
        .order_by(desc(customer.c.created_at))
        .limit(300)
    )).fetchall()
    note_texts = [str(row.notes).strip() for row in note_rows if str(row.notes or "").strip()]
    status_breakdown = [{"status": row.status_name or _enum(row.status) or "unknown", "count": row.count} for row in status_rows]
    note_topics = _extract_note_topics(note_texts, limit=10)
    note_signal_breakdown = _build_note_signal_breakdown(note_texts)
    sales_recommendations = _build_sales_recommendations(
        total_leads=int(total or 0),
        status_breakdown=status_breakdown,
        note_signal_breakdown=note_signal_breakdown,
        notes_count=len(note_texts),
    )
    return {
        "period": period.as_dict(),
        "customer_type_filter": customer_type,
        "total_leads": total,
        "local_leads": local_count,
        "international_leads": intl_count,
        "platform_breakdown": [{"platform": row.platform or "unknown", "count": row.count} for row in platform_rows],
        "status_breakdown": status_breakdown,
        "notes_count": len(note_texts),
        "notes_coverage_percent": round((len(note_texts) / total) * 100, 2) if total else 0.0,
        "top_note_topics": note_topics,
        "note_signal_breakdown": note_signal_breakdown,
        "recent_note_previews": [
            {
                "id": row.id,
                "status": row.status_name or _enum(row.status) or "unknown",
                "platform": row.platform,
                "assistant_name": row.assistant_name,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "note": _clip(row.notes or "", 220),
            }
            for row in note_rows
            if str(row.notes or "").strip()
        ][:6],
        "sales_recommendations": sales_recommendations,
    }


async def _customer_detail_context(session: AsyncSession, customer_match: dict[str, Any]) -> dict[str, Any]:
    row = (await session.execute(
        select(sales_manager_assignment.c.sales_manager_id, user.c.name, user.c.surname, user.c.email)
        .select_from(sales_manager_assignment.outerjoin(user, sales_manager_assignment.c.sales_manager_id == user.c.id))
        .where(and_(sales_manager_assignment.c.customer_id == customer_match["id"], sales_manager_assignment.c.is_active == True))
        .order_by(sales_manager_assignment.c.assigned_at.desc()).limit(1)
    )).fetchone()
    return {
        "customer": customer_match,
        "sales_manager": {"id": row.sales_manager_id, "full_name": f"{row.name} {row.surname}".strip(), "email": row.email} if row else None,
        "notes_preview": _clip(customer_match.get("notes") or "", 260) if customer_match.get("notes") else None,
    }


async def _finance_summary_context(session: AsyncSession, period: PeriodSpec) -> dict[str, Any]:
    current_rate = Decimal(str((await session.execute(select(exchange_rate.c.usd_to_uzs).order_by(exchange_rate.c.updated_at.desc()).limit(1))).scalar() or 12700))
    rows = (await session.execute(select(finance).where(and_(finance.c.date >= period.start_date, finance.c.date <= period.end_date)).order_by(finance.c.date.desc(), finance.c.id.desc()))).fetchall()
    income = Decimal("0"); outcome = Decimal("0"); net = Decimal("0"); real_count = 0; stat_count = 0
    by_card: dict[str, Decimal] = {}; by_service: dict[str, Decimal] = {}
    for row in rows:
        rate = Decimal(str(row.exchange_rate or current_rate or 1)); summ = Decimal(str(row.summ or 0)); donation_uzs = Decimal(str(row.donation or 0))
        summ_uzs = summ * rate if _enum(row.currency) == CurrencyType.USD.value else summ
        real_count += 1 if row.transaction_status == TransactionStatus.real else 0
        stat_count += 0 if row.transaction_status == TransactionStatus.real else 1
        item_net = summ_uzs - donation_uzs if row.type == FinanceType.incomer else -summ_uzs
        income += summ_uzs if row.type == FinanceType.incomer else Decimal("0")
        outcome += summ_uzs if row.type != FinanceType.incomer else Decimal("0")
        net += item_net
        by_card[_enum(row.card) or "unknown"] = by_card.get(_enum(row.card) or "unknown", Decimal("0")) + item_net
        by_service[row.service] = by_service.get(row.service, Decimal("0")) + abs(summ_uzs)
    all_rows = (await session.execute(select(finance))).fetchall()
    balances = {CardType.card1.value: Decimal("0"), CardType.card2.value: Decimal("0"), CardType.card3.value: Decimal("0")}
    for row in all_rows:
        rate = Decimal(str(row.exchange_rate or current_rate or 1)); summ = Decimal(str(row.summ or 0)); donation_uzs = Decimal(str(row.donation or 0))
        donation_cur = donation_uzs / rate if _enum(row.currency) == CurrencyType.USD.value and rate else donation_uzs
        cur_net = summ - donation_cur if row.type == FinanceType.incomer else -summ
        if row.card == CardType.card3:
            balances[CardType.card3.value] += cur_net
        else:
            balances[_enum(row.card)] += cur_net * current_rate if _enum(row.currency) == CurrencyType.USD.value else cur_net
    total_balance_uzs = balances[CardType.card1.value] + balances[CardType.card2.value] + balances[CardType.card3.value] * current_rate
    return {
        "period": period.as_dict(),
        "transactions_count": len(rows),
        "real_transactions_count": real_count,
        "statistical_transactions_count": stat_count,
        "total_income_uzs": _money(income),
        "total_outcome_uzs": _money(outcome),
        "net_flow_uzs": _money(net),
        "current_exchange_rate": _money(current_rate),
        "current_card_balances": {"card1_uzs": _money(balances[CardType.card1.value]), "card2_uzs": _money(balances[CardType.card2.value]), "card3_usd": _money(balances[CardType.card3.value]), "total_balance_uzs": _money(total_balance_uzs)},
        "card_net_breakdown_uzs": {key: _money(value) for key, value in sorted(by_card.items())},
        "top_services": [{"service": key, "amount_uzs": _money(value)} for key, value in sorted(by_service.items(), key=lambda x: x[1], reverse=True)[:7]],
    }


async def _sales_manager_stats_context(session: AsyncSession, sales_manager: dict[str, Any], period: PeriodSpec) -> dict[str, Any]:
    start_uz = datetime(period.start_date.year, period.start_date.month, period.start_date.day, tzinfo=UZ_TZ)
    end_next_uz = datetime(period.end_date.year, period.end_date.month, period.end_date.day, tzinfo=UZ_TZ) + timedelta(days=1)
    start_utc = start_uz.astimezone(timezone.utc).replace(tzinfo=None); end_utc = end_next_uz.astimezone(timezone.utc).replace(tzinfo=None)
    cond = and_(sales_manager_assignment.c.customer_id == customer_status_change_log.c.customer_id, sales_manager_assignment.c.sales_manager_id == sales_manager["id"], sales_manager_assignment.c.is_active == True, or_(sales_manager_assignment.c.assigned_at.is_(None), customer_status_change_log.c.changed_at >= sales_manager_assignment.c.assigned_at))
    assigned = (await session.execute(select(func.count(sales_manager_assignment.c.id)).where(and_(sales_manager_assignment.c.sales_manager_id == sales_manager["id"], sales_manager_assignment.c.is_active == True)))).scalar() or 0
    aggregate = (await session.execute(
        select(
            func.count(customer_status_change_log.c.id).label("total_changes"),
            func.count(func.distinct(customer_status_change_log.c.customer_id)).label("changed_customers"),
            func.count(customer_status_change_log.c.id).filter(customer_status_change_log.c.to_status == CustomerStatus.need_to_call).label("need_to_call"),
            func.count(customer_status_change_log.c.id).filter(customer_status_change_log.c.to_status == CustomerStatus.contacted).label("contacted"),
            func.count(customer_status_change_log.c.id).filter(customer_status_change_log.c.to_status == CustomerStatus.project_started).label("project_started"),
            func.count(customer_status_change_log.c.id).filter(customer_status_change_log.c.to_status == CustomerStatus.continuing).label("continuing"),
            func.count(customer_status_change_log.c.id).filter(customer_status_change_log.c.to_status == CustomerStatus.finished).label("finished"),
            func.count(customer_status_change_log.c.id).filter(customer_status_change_log.c.to_status == CustomerStatus.rejected).label("rejected"),
        )
        .select_from(customer_status_change_log.join(sales_manager_assignment, cond))
        .where(and_(customer_status_change_log.c.changed_at >= start_utc, customer_status_change_log.c.changed_at < end_utc))
    )).fetchone()
    counts = {
        "need_to_call": int(aggregate.need_to_call or 0),
        "contacted": int(aggregate.contacted or 0),
        "project_started": int(aggregate.project_started or 0),
        "continuing": int(aggregate.continuing or 0),
        "finished": int(aggregate.finished or 0),
        "rejected": int(aggregate.rejected or 0),
    }
    total = int(aggregate.total_changes or 0)
    proj_started = int(counts.get("project_started", 0)); finished = int(counts.get("finished", 0))
    return {
        "sales_manager": sales_manager,
        "period": period.as_dict(),
        "assigned_customers": assigned,
        "changed_customers": int(aggregate.changed_customers or 0),
        "total_status_changes": total,
        "to_status_counts": counts,
        "conversion_to_project_started_percent": round((proj_started / total) * 100, 2) if total else 0.0,
        "finish_rate_percent": round((finished / total) * 100, 2) if total else 0.0,
    }


async def _payment_summary_context(session: AsyncSession, period: PeriodSpec) -> dict[str, Any]:
    today = date.today()
    scheduled_rows = (await session.execute(
        select(user_payment.c.id, user_payment.c.project, user_payment.c.date, user_payment.c.summ, user_payment.c.payment)
        .where(and_(user_payment.c.date >= period.start_date, user_payment.c.date <= period.end_date))
        .order_by(user_payment.c.date.asc(), user_payment.c.id.asc())
    )).fetchall()
    total_scheduled = len(scheduled_rows)
    total_amount = sum(Decimal(str(row.summ or 0)) for row in scheduled_rows)
    paid_count = sum(1 for row in scheduled_rows if row.payment)
    unpaid_count = total_scheduled - paid_count
    overdue_rows = (await session.execute(
        select(user_payment.c.id, user_payment.c.project, user_payment.c.date, user_payment.c.summ)
        .where(and_(user_payment.c.date < today, user_payment.c.payment == False))
        .order_by(user_payment.c.date.asc(), user_payment.c.id.asc())
    )).fetchall()
    due_today = (await session.execute(
        select(func.count(user_payment.c.id))
        .where(and_(user_payment.c.date == today, user_payment.c.payment == False))
    )).scalar() or 0
    recurring_rows = (await session.execute(
        select(
            company_recurring_payment.c.id,
            company_recurring_payment.c.title,
            company_recurring_payment.c.amount,
            company_recurring_payment.c.payment_day,
            company_recurring_payment.c.payment_time,
            company_recurring_payment.c.is_active,
        )
        .where(company_recurring_payment.c.is_active == True)
        .order_by(company_recurring_payment.c.payment_day.asc(), company_recurring_payment.c.payment_time.asc())
    )).fetchall()
    recurring_items = []
    for row in recurring_rows:
        next_occurrence = _next_company_payment_occurrence(row.payment_day, row.payment_time, today)
        recurring_items.append({
            "id": row.id,
            "title": row.title,
            "amount": _money(row.amount),
            "payment_day": row.payment_day,
            "payment_time": row.payment_time.strftime("%H:%M:%S") if row.payment_time else None,
            "next_occurrence": next_occurrence.isoformat(),
        })
    recurring_items.sort(key=lambda item: item["next_occurrence"])
    return {
        "period": period.as_dict(),
        "scheduled_payments_count": total_scheduled,
        "scheduled_payments_total": _money(total_amount),
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "due_today_unpaid_count": int(due_today),
        "overdue_unpaid_count": len(overdue_rows),
        "overdue_unpaid_total": _money(sum(Decimal(str(row.summ or 0)) for row in overdue_rows)),
        "recent_overdue_items": [
            {"id": row.id, "project": row.project, "date": row.date.isoformat(), "summ": _money(row.summ)}
            for row in overdue_rows[:7]
        ],
        "upcoming_company_payments": recurring_items[:7],
    }


async def _recall_summary_context(session: AsyncSession, period: PeriodSpec) -> dict[str, Any]:
    now_utc = datetime.utcnow()
    upcoming_24h = now_utc + timedelta(hours=24)
    scheduled_rows = (await session.execute(
        select(customer.c.id, customer.c.full_name, customer.c.platform, customer.c.status, customer.c.recall_time)
        .where(and_(customer.c.recall_time.is_not(None), cast(customer.c.recall_time, Date) >= period.start_date, cast(customer.c.recall_time, Date) <= period.end_date))
        .order_by(customer.c.recall_time.asc())
    )).fetchall()
    overdue_rows = (await session.execute(
        select(customer.c.id, customer.c.full_name, customer.c.platform, customer.c.status, customer.c.recall_time)
        .where(and_(customer.c.recall_time.is_not(None), customer.c.recall_time < now_utc, customer.c.status.notin_([CustomerStatus.finished, CustomerStatus.rejected])))
        .order_by(customer.c.recall_time.asc())
    )).fetchall()
    next_rows = (await session.execute(
        select(customer.c.id, customer.c.full_name, customer.c.platform, customer.c.status, customer.c.recall_time)
        .where(and_(customer.c.recall_time.is_not(None), customer.c.recall_time >= now_utc, customer.c.recall_time <= upcoming_24h))
        .order_by(customer.c.recall_time.asc())
    )).fetchall()
    return {
        "period": period.as_dict(),
        "scheduled_recalls_in_period": len(scheduled_rows),
        "overdue_recalls_count": len(overdue_rows),
        "next_24h_recalls_count": len(next_rows),
        "next_recalls": [
            {
                "id": row.id,
                "full_name": decrypt_text(row.full_name),
                "platform": row.platform,
                "status": _enum(row.status),
                "recall_time": row.recall_time.isoformat() if row.recall_time else None,
            }
            for row in scheduled_rows[:7]
        ],
    }


async def _project_overview_context(session: AsyncSession) -> dict[str, Any]:
    total_projects = (await session.execute(select(func.count(project.c.id)))).scalar() or 0
    total_boards = (await session.execute(select(func.count(project_board.c.id)).where(project_board.c.is_archived == False))).scalar() or 0
    total_cards = (await session.execute(select(func.count(project_board_card.c.id)))).scalar() or 0
    overdue_cards = (await session.execute(select(func.count(project_board_card.c.id)).where(and_(project_board_card.c.due_date.is_not(None), project_board_card.c.due_date < date.today())))).scalar() or 0
    rows = (await session.execute(select(project.c.id, project.c.project_name, func.count(func.distinct(project_member.c.user_id)).label("members_count"), func.count(func.distinct(project_board.c.id)).label("boards_count"), func.count(func.distinct(project_board_card.c.id)).label("cards_count")).select_from(project.outerjoin(project_member, project.c.id == project_member.c.project_id).outerjoin(project_board, project.c.id == project_board.c.project_id).outerjoin(project_board_column, project_board.c.id == project_board_column.c.board_id).outerjoin(project_board_card, project_board_column.c.id == project_board_card.c.column_id)).group_by(project.c.id, project.c.project_name).order_by(project.c.project_name.asc()))).fetchall()
    return {"total_projects": total_projects, "total_boards": total_boards, "total_cards": total_cards, "overdue_cards": overdue_cards, "projects": [{"id": row.id, "project_name": row.project_name, "members_count": row.members_count, "boards_count": row.boards_count, "cards_count": row.cards_count} for row in rows[:20]]}


async def _company_data_hub_context(session: AsyncSession, period: PeriodSpec) -> dict[str, Any]:
    today = date.today()

    user_counts = (
        await session.execute(
            select(
                func.count(user.c.id).label("total_users"),
                func.count(user.c.id).filter(user.c.is_active == True).label("active_users"),  # noqa: E712
                func.count(user.c.id).filter(and_(user.c.is_active == True, user.c.role == UserRole.member)).label("active_members"),  # noqa: E712
                func.count(user.c.id).filter(and_(user.c.is_active == True, user.c.role == UserRole.sales_manager)).label("sales_managers"),  # noqa: E712
            )
        )
    ).fetchone()

    customer_counts = (
        await session.execute(
            select(
                func.count(customer.c.id).label("total_customers"),
                func.count(customer.c.id).filter(
                    and_(
                        cast(customer.c.created_at, Date) >= period.start_date,
                        cast(customer.c.created_at, Date) <= period.end_date,
                    )
                ).label("period_customers"),
                func.count(customer.c.id).filter(customer.c.status == CustomerStatus.need_to_call).label("need_to_call"),
                func.count(customer.c.id).filter(customer.c.status == CustomerStatus.finished).label("finished"),
                func.count(customer.c.id).filter(customer.c.type == CustomerType.international).label("international_customers"),
                func.count(customer.c.id).filter(or_(customer.c.type == CustomerType.local, customer.c.type == None)).label("local_customers"),  # noqa: E711
            )
        )
    ).fetchone()
    today_leads = (
        await session.execute(
            select(func.count(customer.c.id)).where(cast(customer.c.created_at, Date) == today)
        )
    ).scalar() or 0
    overdue_recalls = (
        await session.execute(
            select(func.count(customer.c.id))
            .where(
                and_(
                    customer.c.recall_time.is_not(None),
                    customer.c.recall_time < datetime.utcnow(),
                    customer.c.status.notin_([CustomerStatus.finished, CustomerStatus.rejected]),
                )
            )
        )
    ).scalar() or 0

    update_counts = (
        await session.execute(
            select(
                func.count(daily_update_log.c.id).label("total_updates"),
                func.count(daily_update_log.c.id).filter(daily_update_log.c.is_valid == True).label("valid_updates"),  # noqa: E712
                func.count(func.distinct(daily_update_log.c.user_id)).label("unique_users"),
                func.max(daily_update_log.c.update_date).label("latest_update_date"),
                func.avg(func.length(daily_update_log.c.update_content)).label("avg_update_length"),
            ).where(
                and_(
                    daily_update_log.c.update_date >= period.start_date,
                    daily_update_log.c.update_date <= period.end_date,
                )
            )
        )
    ).fetchone()

    monthly_update_counts = (
        await session.execute(
            select(
                func.count(monthly_update.c.id).label("reports_count"),
                func.avg(monthly_update.c.update_percentage).label("avg_update_percentage"),
                func.sum(monthly_update.c.salary_amount).label("total_salary_amount"),
                func.max(monthly_update.c.update_date).label("latest_report_date"),
            ).where(
                and_(
                    monthly_update.c.update_date >= period.start_date,
                    monthly_update.c.update_date <= period.end_date,
                )
            )
        )
    ).fetchone()

    attendance_counts = (
        await session.execute(
            select(
                func.count(attendance_log.c.id).label("records_count"),
                func.count(func.distinct(attendance_log.c.employee_id)).label("employees_count"),
                func.count(attendance_log.c.id).filter(attendance_log.c.check_out_time.is_not(None)).label("completed_records"),
                func.max(attendance_log.c.attendance_date).label("latest_attendance_date"),
            ).where(
                and_(
                    attendance_log.c.attendance_date >= period.start_date,
                    attendance_log.c.attendance_date <= period.end_date,
                )
            )
        )
    ).fetchone()

    scheduled_payment_counts = (
        await session.execute(
            select(
                func.count(user_payment.c.id).label("scheduled_payments_count"),
                func.sum(user_payment.c.summ).label("scheduled_payments_total"),
                func.count(user_payment.c.id).filter(user_payment.c.payment == True).label("paid_count"),  # noqa: E712
                func.count(user_payment.c.id).filter(user_payment.c.payment == False).label("unpaid_count"),  # noqa: E712
            ).where(
                and_(
                    user_payment.c.date >= period.start_date,
                    user_payment.c.date <= period.end_date,
                )
            )
        )
    ).fetchone()
    due_today_unpaid_count = (
        await session.execute(
            select(func.count(user_payment.c.id)).where(
                and_(
                    user_payment.c.payment == False,  # noqa: E712
                    user_payment.c.date == today,
                )
            )
        )
    ).scalar() or 0
    overdue_unpaid = (
        await session.execute(
            select(
                func.count(user_payment.c.id).label("count"),
                func.sum(user_payment.c.summ).label("total"),
            ).where(
                and_(
                    user_payment.c.payment == False,  # noqa: E712
                    user_payment.c.date < today,
                )
            )
        )
    ).fetchone()

    recurring_payment_counts = (
        await session.execute(
            select(
                func.count(company_recurring_payment.c.id).label("active_count"),
                func.sum(company_recurring_payment.c.amount).label("active_total"),
            ).where(company_recurring_payment.c.is_active == True)  # noqa: E712
        )
    ).fetchone()

    recurring_payment_rows = (
        await session.execute(
            select(
                company_recurring_payment.c.id,
                company_recurring_payment.c.title,
                company_recurring_payment.c.amount,
                company_recurring_payment.c.payment_day,
                company_recurring_payment.c.payment_time,
            )
            .where(company_recurring_payment.c.is_active == True)  # noqa: E712
            .order_by(company_recurring_payment.c.payment_day.asc(), company_recurring_payment.c.payment_time.asc())
        )
    ).fetchall()
    recurring_payment_items = []
    for row in recurring_payment_rows:
        recurring_payment_items.append(
            {
                "id": row.id,
                "title": row.title,
                "amount": _money(row.amount),
                "payment_day": row.payment_day,
                "payment_time": row.payment_time.strftime("%H:%M:%S") if row.payment_time else None,
                "next_occurrence": _next_company_payment_occurrence(row.payment_day, row.payment_time, today).isoformat(),
            }
        )
    recurring_payment_items.sort(key=lambda item: item["next_occurrence"])

    workday_override_counts = (
        await session.execute(
            select(
                func.count(workday_override.c.id).label("total_overrides"),
                func.count(workday_override.c.id).filter(workday_override.c.day_type == "holiday").label("holiday_count"),
                func.count(workday_override.c.id).filter(workday_override.c.day_type == "short_day").label("short_day_count"),
            ).where(
                and_(
                    workday_override.c.special_date >= period.start_date,
                    workday_override.c.special_date <= period.end_date,
                )
            )
        )
    ).fetchone()

    compensation_counts = (
        await session.execute(
            select(
                func.count(compensation_mistake.c.id).label("mistakes_count"),
                func.count(compensation_mistake.c.id).filter(compensation_mistake.c.reached_client == True).label("client_mistakes_count"),  # noqa: E712
            ).where(
                and_(
                    compensation_mistake.c.incident_date >= period.start_date,
                    compensation_mistake.c.incident_date <= period.end_date,
                )
            )
        )
    ).fetchone()
    bonus_records_count = (
        await session.execute(
            select(func.count(compensation_bonus.c.id)).where(
                and_(
                    compensation_bonus.c.award_date >= period.start_date,
                    compensation_bonus.c.award_date <= period.end_date,
                )
            )
        )
    ).scalar() or 0

    severity_rows = (
        await session.execute(
            select(
                compensation_mistake.c.severity,
                func.count(compensation_mistake.c.id).label("count"),
            )
            .where(
                and_(
                    compensation_mistake.c.incident_date >= period.start_date,
                    compensation_mistake.c.incident_date <= period.end_date,
                )
            )
            .group_by(compensation_mistake.c.severity)
            .order_by(desc("count"))
        )
    ).fetchall()

    project_counts = (
        await session.execute(
            select(
                func.count(func.distinct(project.c.id)).label("projects_count"),
                func.count(func.distinct(project_board.c.id)).filter(project_board.c.is_archived == False).label("boards_count"),  # noqa: E712
                func.count(func.distinct(project_board_card.c.id)).label("cards_count"),
                func.count(func.distinct(project_board_card.c.id)).filter(
                    and_(
                        project_board_card.c.due_date.is_not(None),
                        project_board_card.c.due_date < today,
                    )
                ).label("overdue_cards"),
            ).select_from(
                project.outerjoin(project_board, project.c.id == project_board.c.project_id)
                .outerjoin(project_board_column, project_board.c.id == project_board_column.c.board_id)
                .outerjoin(project_board_card, project_board_column.c.id == project_board_card.c.column_id)
            )
        )
    ).fetchone()

    recent_project_rows = (
        await session.execute(
            select(
                project.c.id,
                project.c.project_name,
                func.count(func.distinct(project_member.c.user_id)).label("members_count"),
                func.count(func.distinct(project_board_card.c.id)).label("cards_count"),
            )
            .select_from(
                project.outerjoin(project_member, project.c.id == project_member.c.project_id)
                .outerjoin(project_board, project.c.id == project_board.c.project_id)
                .outerjoin(project_board_column, project_board.c.id == project_board_column.c.board_id)
                .outerjoin(project_board_card, project_board_column.c.id == project_board_card.c.column_id)
            )
            .group_by(project.c.id, project.c.project_name)
            .order_by(project.c.project_name.asc())
            .limit(10)
        )
    ).fetchall()

    return {
        "period": period.as_dict(),
        "company_overview": {
            "active_users": int(user_counts.active_users or 0),
            "total_users": int(user_counts.total_users or 0),
            "active_members": int(user_counts.active_members or 0),
            "sales_managers": int(user_counts.sales_managers or 0),
            "total_customers_all_time": int(customer_counts.total_customers or 0),
            "leads_in_period": int(customer_counts.period_customers or 0),
            "today_leads": int(today_leads or 0),
            "need_to_call_count": int(customer_counts.need_to_call or 0),
            "finished_customers": int(customer_counts.finished or 0),
            "local_customers": int(customer_counts.local_customers or 0),
            "international_customers": int(customer_counts.international_customers or 0),
            "due_payments_today": int(due_today_unpaid_count or 0),
            "overdue_recalls_count": int(overdue_recalls or 0),
        },
        "updates_overview": {
            "valid_updates_in_period": int(update_counts.valid_updates or 0),
            "all_updates_in_period": int(update_counts.total_updates or 0),
            "unique_users_with_updates": int(update_counts.unique_users or 0),
            "latest_update_date": update_counts.latest_update_date.isoformat() if update_counts.latest_update_date else None,
            "average_update_length": round(float(update_counts.avg_update_length or 0), 2),
            "monthly_reports_count": int(monthly_update_counts.reports_count or 0),
            "monthly_reports_average_percentage": round(float(monthly_update_counts.avg_update_percentage or 0), 2),
            "monthly_reports_total_salary_amount": _money(monthly_update_counts.total_salary_amount),
            "latest_monthly_report_date": monthly_update_counts.latest_report_date.isoformat() if monthly_update_counts.latest_report_date else None,
        },
        "attendance_overview": {
            "records_count": int(attendance_counts.records_count or 0),
            "employees_count": int(attendance_counts.employees_count or 0),
            "completed_records_count": int(attendance_counts.completed_records or 0),
            "latest_attendance_date": attendance_counts.latest_attendance_date.isoformat() if attendance_counts.latest_attendance_date else None,
        },
        "payments_overview": {
            "scheduled_payments_count": int(scheduled_payment_counts.scheduled_payments_count or 0),
            "scheduled_payments_total": _money(scheduled_payment_counts.scheduled_payments_total),
            "paid_count": int(scheduled_payment_counts.paid_count or 0),
            "unpaid_count": int(scheduled_payment_counts.unpaid_count or 0),
            "overdue_unpaid_count": int(overdue_unpaid.count or 0),
            "overdue_unpaid_total": _money(overdue_unpaid.total),
            "due_today_unpaid_count": int(due_today_unpaid_count or 0),
            "active_company_payments_count": int(recurring_payment_counts.active_count or 0),
            "active_company_payments_total": _money(recurring_payment_counts.active_total),
            "upcoming_company_payments": recurring_payment_items[:7],
        },
        "workday_overrides_overview": {
            "total_overrides": int(workday_override_counts.total_overrides or 0),
            "holiday_count": int(workday_override_counts.holiday_count or 0),
            "short_day_count": int(workday_override_counts.short_day_count or 0),
        },
        "compensation_overview": {
            "mistakes_count": int(compensation_counts.mistakes_count or 0),
            "client_mistakes_count": int(compensation_counts.client_mistakes_count or 0),
            "bonus_records_count": int(bonus_records_count or 0),
            "severity_breakdown": [
                {"severity": _enum(row.severity), "count": int(row.count or 0)}
                for row in severity_rows
            ],
        },
        "projects_overview": {
            "projects_count": int(project_counts.projects_count or 0),
            "boards_count": int(project_counts.boards_count or 0),
            "cards_count": int(project_counts.cards_count or 0),
            "overdue_cards": int(project_counts.overdue_cards or 0),
            "projects": [
                {
                    "id": row.id,
                    "project_name": row.project_name,
                    "members_count": int(row.members_count or 0),
                    "cards_count": int(row.cards_count or 0),
                }
                for row in recent_project_rows
            ],
        },
    }


async def _company_overview_context(session: AsyncSession, period: PeriodSpec) -> dict[str, Any]:
    active_users = (await session.execute(select(func.count(user.c.id)).where(user.c.is_active == True))).scalar() or 0
    total_users = (await session.execute(select(func.count(user.c.id)))).scalar() or 0
    total_customers = (await session.execute(select(func.count(customer.c.id)))).scalar() or 0
    period_leads = (await session.execute(select(func.count(customer.c.id)).where(and_(cast(customer.c.created_at, Date) >= period.start_date, cast(customer.c.created_at, Date) <= period.end_date)))).scalar() or 0
    today = date.today()
    today_leads = (await session.execute(select(func.count(customer.c.id)).where(cast(customer.c.created_at, Date) == today))).scalar() or 0
    need_to_call_count = (await session.execute(select(func.count(customer.c.id)).where(customer.c.status == CustomerStatus.need_to_call))).scalar() or 0
    due_payments_today = (await session.execute(select(func.count(user_payment.c.id)).where(and_(user_payment.c.date == today, user_payment.c.payment == False)))).scalar() or 0
    overdue_recalls = (await session.execute(
        select(func.count(customer.c.id))
        .where(and_(customer.c.recall_time.is_not(None), customer.c.recall_time < datetime.utcnow(), customer.c.status.notin_([CustomerStatus.finished, CustomerStatus.rejected])))
    )).scalar() or 0
    sales_managers = (await session.execute(select(func.count(user.c.id)).where(user.c.role == UserRole.sales_manager))).scalar() or 0
    return {
        "period": period.as_dict(),
        "active_users": active_users,
        "total_users": total_users,
        "sales_managers": sales_managers,
        "total_customers_all_time": total_customers,
        "leads_in_period": period_leads,
        "today_leads": today_leads,
        "need_to_call_count": need_to_call_count,
        "due_payments_today": due_payments_today,
        "overdue_recalls_count": overdue_recalls,
    }


async def build_cims_ai_context(session: AsyncSession, question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    history_user_text = _history_text(history, roles=("user",), limit=3)
    resolution_text = question.strip()
    if history_user_text and _is_follow_up_question(question):
        resolution_text = f"{history_user_text}\n{question.strip()}".strip()
    period = _resolve_period(question if _has_explicit_period(question) else resolution_text)
    employee = await _match_user(session, question) or (await _match_user(session, resolution_text) if resolution_text != question else None)
    sales_manager = await _match_user(session, question, UserRole.sales_manager) or (
        await _match_user(session, resolution_text, UserRole.sales_manager) if resolution_text != question else None
    )
    customer_match = await _match_customer(session, question) or (await _match_customer(session, resolution_text) if resolution_text != question else None)
    customer_type = _detect_customer_type(question) or _detect_customer_type(resolution_text)
    intents = _detect_actions(resolution_text, employee, sales_manager, customer_match)
    data_hub = await _company_data_hub_context(session, period)
    context: dict[str, Any] = {
        "question": question.strip(),
        "resolved_question": resolution_text,
        "period": period.as_dict(),
        "intents": intents,
        "employee": employee,
        "sales_manager": sales_manager,
        "customer_match": customer_match,
        "customer_type_filter": customer_type,
        "data_hub": data_hub,
    }
    if "employee_update" in intents and employee:
        context["employee_update"] = await _employee_update_context(session, employee, period)
    if "lead_stats" in intents:
        context["lead_stats"] = await _lead_stats_context(session, period, customer_type)
    if "customer_detail" in intents and customer_match:
        context["customer_detail"] = await _customer_detail_context(session, customer_match)
    if "finance_summary" in intents:
        context["finance_summary"] = await _finance_summary_context(session, period)
    if "payment_summary" in intents:
        context["payment_summary"] = {
            "period": period.as_dict(),
            **data_hub.get("payments_overview", {}),
        }
    if "recall_summary" in intents:
        context["recall_summary"] = await _recall_summary_context(session, period)
    if "sales_manager_stats" in intents and sales_manager:
        context["sales_manager_stats"] = await _sales_manager_stats_context(session, sales_manager, period)
    if "project_overview" in intents:
        context["project_overview"] = await _project_overview_context(session)
    if "company_overview" in intents:
        context["company_overview"] = {
            "period": period.as_dict(),
            **data_hub.get("company_overview", {}),
        }
    return context


def build_cims_ai_fallback_answer(context: dict[str, Any]) -> str:
    out: list[str] = []
    employee_update = context.get("employee_update")
    if employee_update:
        report = employee_update.get("monthly_report_summary")
        if report:
            out.append(f"{employee_update['employee']['full_name']} uchun {employee_update['period']['label']} bo'yicha report o'rtacha foizi {report['average_update_percentage']}%. Reportlar soni {report['reports_count']} ta.")
        out.append(f"Ish kunlari bo'yicha hisoblanganda update foizi {employee_update['calculated_update_percentage']}%: {employee_update['update_days']} ta update kuni va {employee_update['working_days']} ta ish kuni.")
    lead_stats = context.get("lead_stats")
    if lead_stats:
        out.append(f"{lead_stats['period']['label']} davrida jami {lead_stats['total_leads']} ta lead kelgan. Local {lead_stats['local_leads']} ta, international {lead_stats['international_leads']} ta.")
        if lead_stats.get("notes_count"):
            out.append(
                f"Leadlardan {lead_stats['notes_count']} tasida note bor ({lead_stats['notes_coverage_percent']}%). "
                f"Note lardagi asosiy mavzular: {', '.join(item['keyword'] for item in lead_stats.get('top_note_topics', [])[:5]) or 'aniq signal yoq'}."
            )
        if lead_stats.get("sales_recommendations"):
            out.append("Savdo bo'yicha tavsiyalar:")
            out.extend(lead_stats["sales_recommendations"][:4])
    customer_detail = context.get("customer_detail")
    if customer_detail:
        item = customer_detail["customer"]
        out.append(f"Mijoz: {item['full_name']} | status: {item['status']} | platforma: {item['platform']} | telefon: {item['phone_number']}.")
        if customer_detail.get("notes_preview"):
            out.append(f"Oxirgi muhim note: {customer_detail['notes_preview']}")
    finance_summary = context.get("finance_summary")
    if finance_summary:
        out.append(f"{finance_summary['period']['label']} davrida kirim {finance_summary['total_income_uzs']} UZS, chiqim {finance_summary['total_outcome_uzs']} UZS, net flow {finance_summary['net_flow_uzs']} UZS.")
        out.append(f"Hozirgi umumiy balans {finance_summary['current_card_balances']['total_balance_uzs']} UZS.")
    payment_summary = context.get("payment_summary")
    if payment_summary:
        out.append(
            f"{payment_summary['period']['label']} davrida rejalashtirilgan to'lovlar {payment_summary['scheduled_payments_count']} ta, "
            f"shundan {payment_summary['paid_count']} ta to'langan va {payment_summary['unpaid_count']} ta to'lanmagan."
        )
        out.append(
            f"Bugun muddatli to'lanmagan to'lovlar {payment_summary['due_today_unpaid_count']} ta, "
            f"umumiy overdue to'lovlar {payment_summary['overdue_unpaid_count']} ta."
        )
    recall_summary = context.get("recall_summary")
    if recall_summary:
        out.append(
            f"{recall_summary['period']['label']} davrida recall belgilangan mijozlar {recall_summary['scheduled_recalls_in_period']} ta. "
            f"Hozir overdue recall {recall_summary['overdue_recalls_count']} ta, keyingi 24 soatda {recall_summary['next_24h_recalls_count']} ta recall bor."
        )
    sales_manager_stats = context.get("sales_manager_stats")
    if sales_manager_stats:
        out.append(
            f"{sales_manager_stats['sales_manager']['full_name']} uchun {sales_manager_stats['period']['label']} davrida "
            f"{sales_manager_stats['total_status_changes']} ta status o'zgarishi va {sales_manager_stats['changed_customers']} ta mijozda harakat bo'lgan. "
            f"Project started conversion {sales_manager_stats['conversion_to_project_started_percent']}%."
        )
    project_overview = context.get("project_overview")
    if project_overview:
        out.append(f"Projects moduli bo'yicha jami {project_overview['total_projects']} ta project, {project_overview['total_boards']} ta board va {project_overview['total_cards']} ta card bor.")
    company_overview = context.get("company_overview")
    if company_overview:
        out.append(
            f"CIMS bo'yicha active userlar {company_overview['active_users']} ta, jami userlar {company_overview['total_users']} ta, "
            f"sales managerlar {company_overview['sales_managers']} ta, jami customerlar {company_overview['total_customers_all_time']} ta."
        )
        out.append(
            f"{company_overview['period']['label']} davrida {company_overview['leads_in_period']} ta lead kelgan, "
            f"bugun {company_overview['today_leads']} ta lead, need_to_call {company_overview['need_to_call_count']} ta, "
            f"due payment bugun {company_overview['due_payments_today']} ta."
        )
    data_hub = context.get("data_hub")
    if data_hub and not out:
        company = data_hub.get("company_overview", {})
        updates = data_hub.get("updates_overview", {})
        payments = data_hub.get("payments_overview", {})
        projects = data_hub.get("projects_overview", {})
        out.append(
            f"CIMS umumiy ko'rsatkichlari: active userlar {company.get('active_users', 0)} ta, "
            f"jami customerlar {company.get('total_customers_all_time', 0)} ta, "
            f"davr bo'yicha valid update lar {updates.get('valid_updates_in_period', 0)} ta."
        )
        out.append(
            f"Active company paymentlar {payments.get('active_company_payments_count', 0)} ta, "
            f"ularning umumiy summasi {payments.get('active_company_payments_total', 0)}."
        )
        out.append(
            f"Projectlar {projects.get('projects_count', 0)} ta, cardlar {projects.get('cards_count', 0)} ta, "
            f"overdue cardlar {projects.get('overdue_cards', 0)} ta."
        )
    sql_analytics = context.get("sql_analytics")
    if sql_analytics and sql_analytics.get("rows_preview"):
        preview = sql_analytics["rows_preview"][:3]
        out.append(
            f"Dynamic analytics ({sql_analytics.get('reason', 'sql')}): "
            f"{json.dumps(preview, ensure_ascii=False, default=str)}"
        )
    return "\n".join(out) if out else "Savol bo'yicha yetarli analytics context topilmadi."


async def _generate_sql_analytics(
    session: AsyncSession,
    question: str,
    context: dict[str, Any],
    history: list[dict[str, str]] | None,
    *,
    api_key: str,
    model: str,
    base_url: str,
) -> Optional[dict[str, Any]]:
    history_text = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}"
        for item in (history or [])[-4:]
        if item.get("content")
    ) or "yoq"
    prompt = (
        "Siz CIMS analytics SQL generator siz. Faqat PostgreSQL uchun bitta xavfsiz SELECT yoki WITH query yozing. "
        "Hech qachon INSERT/UPDATE/DELETE/DDL yozmang. Faqat quyidagi jadvallar va ustunlardan foydalaning.\n"
        f"{_schema_brief()}\n\n"
        f"Savol: {question}\n"
        f"Oldingi chat: {history_text}\n"
        f"Oldindan yig'ilgan context: {json.dumps(context, ensure_ascii=False, default=str)}\n\n"
        "JSON formatda javob bering: "
        '{"sql":"...","reason":"qisqa sabab","should_run":true}. '
        "Agar SQL kerak bo'lmasa should_run false qiling."
    )
    payload = {
        "model": model,
        "temperature": 0,
        "max_output_tokens": 500,
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]}
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(f"{base_url.rstrip('/')}/responses", json=payload, headers=headers)
            response.raise_for_status()
            raw = _extract_response_text(response.json())
        parsed = _extract_json_object(raw or "")
        if not parsed or not parsed.get("should_run"):
            return None
        sql = str(parsed.get("sql") or "").strip()
        if not _is_safe_select_sql(sql):
            return None
        result = await session.execute(text(sql))
        rows = result.mappings().all()
        preview = [dict(row) for row in rows[:50]]
        return {
            "reason": str(parsed.get("reason") or "").strip() or "dynamic sql analytics",
            "sql": sql,
            "row_count": len(rows),
            "rows_preview": preview,
        }
    except Exception:
        return None


async def generate_cims_ai_answer(
    session: AsyncSession,
    question: str,
    context: dict[str, Any],
    history: list[dict[str, str]] | None = None,
) -> tuple[str, bool]:
    api_key = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
    model = os.getenv("OPENAI_MODEL", OPENAI_MODEL)
    base_url = os.getenv("OPENAI_BASE_URL", OPENAI_BASE_URL)
    fallback = build_cims_ai_fallback_answer(context)
    if not api_key:
        return fallback, False
    sql_analytics = None
    if _should_run_sql_analytics(question, context):
        sql_analytics = await _generate_sql_analytics(
            session,
            question,
            context,
            history,
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
    if sql_analytics:
        context["sql_analytics"] = sql_analytics
    history_text = "\n".join(f"{item.get('role', 'user')}: {item.get('content', '')}" for item in (history or [])[-6:] if item.get("content")) or "yoq"
    system_prompt = (
        "Siz CIMS AI analytics agent siz. Faqat berilgan CIMS context asosida javob bering. "
        "O'zbek tilida aniq raqamlar bilan yozing. Bir nechta savol bo'lsa, hammasiga javob bering. "
        "Ma'lumot yetmasa buni aniq ayting. Agar contextda customer notes bo'lsa, ularni o'qib amaliy xulosa va sotuv bo'yicha tavsiya bering. "
        "Faqat statistikani sanab chiqish bilan cheklanib qolmang: kerak bo'lsa 2-5 ta aniq action point bering. "
        "Xom DB iboralarini ishlatmang, masalan `unique customer` o'rniga oddiy biznes tilida yozing."
    )
    user_prompt = (
        f"Savol: {question}\n\n"
        f"Oldingi chat:\n{history_text}\n\n"
        f"CIMS context:\n{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
        "Endi foydalanuvchiga ishonchli, qisqa, tahlilli va kerak bo'lsa amaliy tavsiyali javob yozing."
    )
    payload = {"model": model, "temperature": 0.2, "max_output_tokens": 700, "input": [{"role": "system", "content": [{"type": "input_text", "text": system_prompt}]}, {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}]}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            response = await client.post(f"{base_url.rstrip('/')}/responses", json=payload, headers=headers)
            response.raise_for_status()
            text = _extract_response_text(response.json())
            if text:
                return text.strip(), True
    except Exception:
        pass
    return fallback, False
