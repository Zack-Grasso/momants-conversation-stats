from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

import httpx

from app.integrations.momants_client import get_momants_client

logger = logging.getLogger(__name__)

DUTCH_MONTHS = {
    1: "jan",
    2: "feb",
    3: "mrt",
    4: "apr",
    5: "mei",
    6: "jun",
    7: "jul",
    8: "aug",
    9: "sep",
    10: "okt",
    11: "nov",
    12: "dec",
}


DUTCH_WEEKDAYS = {
    0: "maandag",
    1: "dinsdag",
    2: "woensdag",
    3: "donderdag",
    4: "vrijdag",
    5: "zaterdag",
    6: "zondag",
}


class MessageLike(Protocol):
    source_created_at: datetime | None
    created_at: datetime


class ConversationLike(Protocol):
    messages: list[MessageLike]


def resolve_event_name(agent_id: str, event_name: str | None) -> tuple[str, list[str]]:
    override = (event_name or "").strip()
    if override:
        return override, []

    try:
        data = get_momants_client().get_agent(agent_id)
        name = (data.get("name") or "").strip()
        if name:
            return name, []
    except httpx.HTTPStatusError as exc:
        logger.warning("Momants agent lookup failed for %s: %s", agent_id, exc)
    except Exception as exc:
        logger.warning("Could not resolve event name for %s: %s", agent_id, exc)

    return f"Agent {agent_id[:8]}", ["event_name"]


def all_message_timestamps(conversations: list[ConversationLike]) -> list[datetime]:
    timestamps: list[datetime] = []
    for conversation in conversations:
        for message in conversation.messages:
            timestamp = message.source_created_at or message.created_at
            if timestamp is not None:
                timestamps.append(timestamp)
    return timestamps


def format_short_date(value: datetime) -> str:
    return f"{value.day} {DUTCH_MONTHS[value.month]}"


def format_date_range(timestamps: list[datetime]) -> str:
    start = min(timestamps)
    end = max(timestamps)
    if start.date() == end.date():
        return format_short_date(start)
    return f"{format_short_date(start)} – {format_short_date(end)}"


def format_report_num(value: float | int, digits: int) -> str:
    if isinstance(value, int) or value == int(value):
        return str(int(value))
    formatted = f"{float(value):.{digits}f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def format_dutch_int(value: float | int) -> str:
    number = int(round(float(value)))
    return f"{number:,}".replace(",", ".")


def format_support_hours(value: float | int | None) -> str:
    """Format saved support hours without rounding 828,75 up to 829."""
    if value is None:
        return "—"
    amount = float(value)
    if abs(amount - round(amount)) < 0.05:
        return format_dutch_int(round(amount))
    whole = int(amount)
    decimals = int(round((amount - whole) * 100))
    whole_fmt = format_dutch_int(whole) if whole >= 1000 else str(whole)
    return f"{whole_fmt},{decimals:02d}"


def format_dutch_decimal(value: float, *, digits: int = 1) -> str:
    formatted = format_report_num(value, digits) or str(value)
    return formatted.replace(".", ",")


def format_eur(value: float | int | None, *, compact: bool = True) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if compact:
        if abs(amount) >= 1_000_000:
            return f"€{format_report_num(amount / 1_000_000, 1)}m"
        if abs(amount) >= 1_000:
            return f"€{format_report_num(amount / 1_000, 0)}k"
        return f"€{format_report_num(amount, 0)}"
    if amount == int(amount):
        return f"€{format_dutch_int(amount)}"
    whole = int(amount)
    cents = int(round((amount - whole) * 100))
    return f"€{format_dutch_int(whole)},{cents:02d}"
