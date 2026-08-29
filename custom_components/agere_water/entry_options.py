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

from .const import (
    CONF_NEXT_READING_DATE, CONF_READINGS, CONF_TARIFFS,
    CONF_TARIFFS_SEEDED_THROUGH,
)
from .readings import SOURCE_MANUAL, Reading, ReadingLog
from .tariffs import BUILTIN_SCHEDULE, Tariff, TariffPeriod, TariffSchedule

_TARIFF_VALUES = (
    "water_availability", "sanitation_drainage", "sanitation_availability",
    "waste_variable", "waste_fixed", "tax_water", "tax_sanitation",
    "tax_waste_mgmt",
)


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


def format_decimal(value: Decimal) -> str:
    """Shortest exact decimal form, without float noise or exponents.

    Values reach us as floats from the service schema and the number selector,
    so 543 arrives as Decimal("543.0") and would be stored — and shown — as
    "543.0". `normalize()` alone is not enough: it renders 500.0 as "5E+2".
    """
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return str(value.normalize())


def readings_to_options(log: ReadingLog) -> list[dict[str, str]]:
    """Serialise a ReadingLog for storage in options."""
    return [
        {"date": r.date.isoformat(), "m3": format_decimal(r.m3), "source": r.source}
        for r in log.readings
    ]


def next_reading_date_from_options(options: Mapping[str, Any]) -> date | None:
    raw = options.get(CONF_NEXT_READING_DATE)
    return date.fromisoformat(raw) if raw else None


def _decimal(raw: object, field: str, when: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError) as err:
        raise ValueError(f"tariff {when}: invalid {field} {raw!r}") from err


def tariffs_from_options(options: Mapping[str, Any]) -> TariffSchedule:
    """Build the schedule from stored options, or the built-in one if absent."""
    stored = options.get(CONF_TARIFFS) or []
    if not stored:
        return BUILTIN_SCHEDULE
    periods = []
    for raw in stored:
        try:
            when = date.fromisoformat(raw["effective_from"])
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f"invalid tariff effective date {raw!r}") from err
        label = when.isoformat()
        try:
            bounds = tuple(int(b) for b in raw["water_tier_bounds"])
            prices = tuple(
                None if p is None else _decimal(p, "water_tier_prices", label)
                for p in raw["water_tier_prices"]
            )
            values = {
                name: _decimal(raw[name], name, label) for name in _TARIFF_VALUES
            }
        except (KeyError, TypeError) as err:
            raise ValueError(f"tariff {label}: missing {err}") from err
        periods.append(TariffPeriod(
            effective_from=when,
            tariff=Tariff(
                water_tier_bounds=bounds, water_tier_prices=prices, **values
            ),
        ))
    return TariffSchedule(periods)


def tariffs_to_options(schedule: TariffSchedule) -> list[dict[str, Any]]:
    """Serialise the schedule for storage in options."""
    out = []
    for p in schedule.periods:
        t = p.tariff
        entry: dict[str, Any] = {
            "effective_from": p.effective_from.isoformat(),
            "water_tier_bounds": list(t.water_tier_bounds),
            "water_tier_prices": [
                None if v is None else format_decimal(v)
                for v in t.water_tier_prices
            ],
        }
        for name in _TARIFF_VALUES:
            entry[name] = format_decimal(getattr(t, name))
        out.append(entry)
    return out


def seeded_through_from_options(options: Mapping[str, Any]) -> date | None:
    raw = options.get(CONF_TARIFFS_SEEDED_THROUGH)
    return date.fromisoformat(raw) if raw else None
