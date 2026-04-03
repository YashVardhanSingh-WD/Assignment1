from datetime import datetime, timezone
import math
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SERVICE_OPTIONS = [
    ("HANDWRITTEN", "Handwritten Assignment"),
    ("PRESENTATION", "PowerPoint Presentation"),
    ("WORD_DOCUMENT", "Word Document / Report"),
    ("RESEARCH_SUPPORT", "Research / Analysis Task"),
    ("OTHER", "Other Academic Task"),
]

SUBMISSION_MODES = [
    ("ONLINE", "Online Submission"),
    ("HANDWRITTEN", "Physical Handwritten Delivery"),
]

COMPLEXITY_OPTIONS = [
    ("BASIC", "Basic"),
    ("STANDARD", "Standard"),
    ("ADVANCED", "Advanced"),
    ("EXPERT", "Expert"),
]

PAYMENT_METHODS = [
    ("UPI", "UPI"),
    ("CARD", "Debit / Credit Card"),
    ("NET_BANKING", "Net Banking"),
]

STATUS_LABELS = {
    "QUOTE_PENDING": "Waiting for final price",
    "NEW": "Awaiting payment",
    "PAID": "Ready for worker pickup",
    "ASSIGNED": "Claimed by worker",
    "IN_PROGRESS": "In progress",
    "COMPLETED": "Waiting for student approval",
    "APPROVED": "Approved and closed",
    "CANCELLED": "Cancelled",
}

STATUS_TONES = {
    "QUOTE_PENDING": "warning",
    "NEW": "muted",
    "PAID": "accent",
    "ASSIGNED": "accent",
    "IN_PROGRESS": "warning",
    "COMPLETED": "success",
    "APPROVED": "success",
    "CANCELLED": "danger",
}

SERVICE_BASE_RATES = {
    "HANDWRITTEN": 55,
    "PRESENTATION": 90,
    "WORD_DOCUMENT": 70,
    "RESEARCH_SUPPORT": 120,
    "OTHER": 85,
}

COMPLEXITY_MULTIPLIERS = {
    "BASIC": 1.0,
    "STANDARD": 1.2,
    "ADVANCED": 1.5,
    "EXPERT": 1.8,
}

SUBMISSION_MODE_MULTIPLIERS = {
    "ONLINE": 1.0,
    "HANDWRITTEN": 1.25,
}


def get_timezone(timezone_name: str):
    for candidate in (timezone_name, "Asia/Calcutta", "Asia/Kolkata", "UTC"):
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    return timezone.utc


def now_iso(timezone_name: str) -> str:
    return datetime.now(get_timezone(timezone_name)).replace(second=0, microsecond=0).isoformat()


def parse_local_deadline(deadline_value: str, timezone_name: str) -> datetime:
    local_timezone = get_timezone(timezone_name)
    deadline = datetime.fromisoformat(deadline_value)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=local_timezone)
    return deadline.astimezone(local_timezone)


def calculate_estimate(
    service_type: str,
    complexity: str,
    page_count: int,
    deadline_value: str,
    submission_mode: str,
    timezone_name: str,
) -> dict[str, float | int]:
    deadline = parse_local_deadline(deadline_value, timezone_name)
    current_time = datetime.now(get_timezone(timezone_name))
    hours_left = max((deadline - current_time).total_seconds() / 3600, 1)

    urgency_multiplier = 1.0
    if hours_left <= 12:
        urgency_multiplier = 1.8
    elif hours_left <= 24:
        urgency_multiplier = 1.6
    elif hours_left <= 48:
        urgency_multiplier = 1.4
    elif hours_left <= 72:
        urgency_multiplier = 1.2

    base_rate = SERVICE_BASE_RATES.get(service_type, SERVICE_BASE_RATES["OTHER"])
    complexity_multiplier = COMPLEXITY_MULTIPLIERS.get(complexity, 1.2)
    mode_multiplier = SUBMISSION_MODE_MULTIPLIERS.get(submission_mode, 1.0)
    raw_total = page_count * base_rate * complexity_multiplier * mode_multiplier * urgency_multiplier
    total = int(math.ceil(raw_total / 50.0) * 50)

    return {
        "total": total,
        "base_rate": base_rate,
        "urgency_multiplier": urgency_multiplier,
        "complexity_multiplier": complexity_multiplier,
        "mode_multiplier": mode_multiplier,
        "hours_left": int(hours_left),
    }


def normalize_phone(value: str) -> str:
    return re.sub(r"[^\d+]", "", value).strip()


def generate_public_id(timezone_name: str) -> str:
    stamp = datetime.now(get_timezone(timezone_name)).strftime("%Y%m%d")
    return f"AW-{stamp}-{uuid4().hex[:6].upper()}"


def worker_payout_amount(price: float | int, share: float) -> float:
    return round(float(price) * share, 2)


def owner_commission_amount(price: float | int, share: float) -> float:
    return round(float(price) - worker_payout_amount(price, share), 2)


def label_for(options: list[tuple[str, str]], value: str) -> str:
    return dict(options).get(value, value.title().replace("_", " "))


def status_tone(status: str) -> str:
    return STATUS_TONES.get(status, "muted")
