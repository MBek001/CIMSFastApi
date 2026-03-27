from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from models.user_models import CompensationBonusType, MistakeCategory, MistakeSeverity

MAX_MONTHLY_DEDUCTION_PERCENT = Decimal("40")
DEVELOPER_SHARE_PERCENT = Decimal("70")
REVIEWER_SHARE_PERCENT = Decimal("30")

DEDUCTION_RATE_BY_SEVERITY = {
    MistakeSeverity.minor: Decimal("2"),
    MistakeSeverity.moderate: Decimal("5"),
    MistakeSeverity.major: Decimal("10"),
    MistakeSeverity.critical: Decimal("20"),
}

DELIVERY_BONUS_RATE_BY_TYPE = {
    CompensationBonusType.early_delivery: Decimal("4"),
    CompensationBonusType.major_early_delivery: Decimal("7"),
}

QUALITY_BONUS_NO_CLIENT_MISTAKES = Decimal("7")
QUALITY_BONUS_NO_MAJOR_CRITICAL = Decimal("4")
PRODUCTIVITY_BONUS_FULL_UPDATES = Decimal("10")

CATEGORY_LABELS = {
    MistakeCategory.ai_integration: "AI Integration",
    MistakeCategory.backend: "Backend",
    MistakeCategory.frontend: "Frontend",
    MistakeCategory.mobile: "Mobile",
    MistakeCategory.devops: "DevOps / Deployment",
    MistakeCategory.security: "Security",
    MistakeCategory.performance: "Performance & Optimization",
    MistakeCategory.client_impact: "Client Impact",
}


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def as_money(value: Decimal | float | int) -> float:
    return float(quantize_money(Decimal(str(value))))


def normalize_base_salary(value: Decimal | float | int | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return quantize_money(Decimal(str(value)))


def deduction_rate_for_severity(severity: MistakeSeverity) -> Decimal:
    return DEDUCTION_RATE_BY_SEVERITY[severity]


def deduction_amount_for_severity(base_salary: Decimal | float | int | None, severity: MistakeSeverity) -> Decimal:
    salary_base = normalize_base_salary(base_salary)
    return quantize_money(salary_base * deduction_rate_for_severity(severity) / Decimal("100"))


def max_monthly_deduction_amount(base_salary: Decimal | float | int | None) -> Decimal:
    salary_base = normalize_base_salary(base_salary)
    return quantize_money(salary_base * MAX_MONTHLY_DEDUCTION_PERCENT / Decimal("100"))


def delivery_bonus_rate(bonus_type: CompensationBonusType) -> Decimal:
    return DELIVERY_BONUS_RATE_BY_TYPE[bonus_type]


def bonus_amount_from_percent(base_salary: Decimal | float | int | None, percent: Decimal) -> Decimal:
    salary_base = normalize_base_salary(base_salary)
    return quantize_money(salary_base * percent / Decimal("100"))


def proportional_cap(raw_amounts: Iterable[Decimal], cap_amount: Decimal) -> list[Decimal]:
    raw_list = [quantize_money(Decimal(str(amount))) for amount in raw_amounts]
    total_raw = sum(raw_list, Decimal("0"))
    if total_raw <= cap_amount:
        return raw_list
    if total_raw <= 0:
        return [Decimal("0.00") for _ in raw_list]

    scale = cap_amount / total_raw
    applied: list[Decimal] = []
    running_total = Decimal("0")
    for index, raw in enumerate(raw_list):
        if index == len(raw_list) - 1:
            item_amount = quantize_money(cap_amount - running_total)
        else:
            item_amount = quantize_money(raw * scale)
            running_total += item_amount
        applied.append(max(item_amount, Decimal("0.00")))
    return applied
