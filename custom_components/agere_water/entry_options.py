"""Mapping between `entry.options` and the pure reading-log model.

Readings live in `entry.options` rather than in a `Store`: the options flow
writes them natively and the entry's update listener already reloads and
recomputes on change.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .const import CONF_NEXT_READING_DATE, CONF_READINGS
from .readings import SOURCE_MANUAL, Reading, ReadingLog


def readings_from_options(options: Mapping[str, Any]) -> ReadingLog:
    """Build a validated ReadingLog from stored options. Raises ValueError."""
    stored = options.get(CONF_READINGS) or []
    readings = []
    for raw in stored:
        try:
            m3 = Decimal(str(raw["m3"]))
        except (InvalidOperation, KeyError, TypeError) as err:
            raise ValueError(f"invalid stored reading {raw!r}") from err
        try:
            when = date.fromisoformat(raw["date"])
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f"invalid stored reading date {raw!r}") from err
        readings.append(
            Reading(date=when, m3=m3, source=raw.get("source", SOURCE_MANUAL))
        )
    return ReadingLog(readings)


def readings_to_options(log: ReadingLog) -> list[dict[str, str]]:
    """Serialise a ReadingLog for storage in options."""
    return [
        {"date": r.date.isoformat(), "m3": str(r.m3), "source": r.source}
        for r in log.readings
    ]


def next_reading_date_from_options(options: Mapping[str, Any]) -> date | None:
    raw = options.get(CONF_NEXT_READING_DATE)
    return date.fromisoformat(raw) if raw else None
