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


@dataclass
class Breakdown:
    water: Decimal
    sanitation: Decimal
    waste: Decimal
    taxes: Decimal
    base_without_vat: Decimal
    vat: Decimal
    total: Decimal
    lines: list[TierLine]


def calcular(consumo: Decimal, days: int, config: CalcConfig) -> Breakdown:
    """Compute the AGERE bill breakdown for the given cycle consumption and days."""
    t = config.tariff
    lines = water_lines(consumo, days, t)

    # component subtotals (each line already rounded to cents)
    water_consumption = sum((l.value for l in lines), Decimal(0))
    water = (water_consumption + money(t.water_availability)) if config.include_water else Decimal(0)

    san_drain = money(consumo * t.sanitation_drainage)
    sanitation = (san_drain + money(t.sanitation_availability)) if config.include_sanitation else Decimal(0)

    waste = (money(consumo * t.waste_variable) + money(t.waste_fixed)) if config.include_waste else Decimal(0)

    tax_water = money(consumo * t.tax_water)
    tax_sanit = money(consumo * t.tax_sanitation)
    taxes = (tax_water + tax_sanit + money(t.tax_waste_mgmt)) if config.include_taxes else Decimal(0)

    base = water + sanitation + waste + taxes

    # VAT: water (all), sanitation (all), the two resource taxes. Never waste / waste-mgmt tax.
    vat_base = Decimal(0)
    if config.include_water:
        vat_base += water
    if config.include_sanitation:
        vat_base += sanitation
    if config.include_taxes:
        vat_base += tax_water + tax_sanit
    vat = money(vat_base * config.vat_rate) if config.include_vat else Decimal(0)

    return Breakdown(water, sanitation, waste, taxes, money(base), vat, money(base + vat), lines)
