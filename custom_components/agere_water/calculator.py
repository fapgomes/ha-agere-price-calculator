"""Pure AGERE Doméstico billing engine. No Home Assistant dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .const import CalcConfig, Tariff

_CENT = Decimal("0.01")
_TENTHOUSANDTH = Decimal("0.0001")
_THIRTY = Decimal("30")


def money(value: Decimal) -> Decimal:
    """Round a monetary value to cents, half up (AGERE per-line rule)."""
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def price4(value: Decimal) -> Decimal:
    """Round a unit price to 4 decimals, half up."""
    return Decimal(value).quantize(_TENTHOUSANDTH, rounding=ROUND_HALF_UP)


def tier_limits(days: int, bounds: tuple[int, ...]) -> list[int]:
    """Prorate the per-30-day tier limits to the elapsed days, rounded half up."""
    return [
        int((Decimal(b) * days / _THIRTY).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        for b in bounds
    ]


@dataclass
class TierLine:
    index: int
    qty: Decimal
    rate: Decimal
    value: Decimal


def water_lines(consumo: Decimal, days: int, tariff: Tariff) -> list[TierLine]:
    """Split consumption across the (prorated) water tiers, one TierLine each."""
    limits = tier_limits(days, tariff.water_tier_bounds)
    lowers = [Decimal(0)] + [Decimal(v) for v in limits]           # 5 lower bounds
    uppers = [Decimal(v) for v in limits] + [None]                 # 5 upper bounds (last = inf)
    lines: list[TierLine] = []
    for i, (lo, up, rate) in enumerate(zip(lowers, uppers, tariff.water_tier_prices)):
        cap = consumo if up is None else min(consumo, up)
        qty = max(Decimal(0), cap - lo)
        lines.append(TierLine(i, qty, rate, money(qty * rate)))
    return lines
