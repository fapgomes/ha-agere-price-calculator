"""Pure AGERE Doméstico billing engine. No Home Assistant dependencies."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .const import CalcConfig
from .tariffs import (
    FIXED_COMPONENTS, VARIABLE_COMPONENTS, TariffSchedule, UnknownTariffValue,
)

_CENT = Decimal("0.01")
_TENTHOUSANDTH = Decimal("0.0001")
_THIRTY = Decimal("30")


def money(value: Decimal) -> Decimal:
    """Round a monetary value to cents, half up (AGERE per-line rule)."""
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def price4(value: Decimal) -> Decimal:
    """Round a unit price to 4 decimals, half up."""
    return Decimal(value).quantize(_TENTHOUSANDTH, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SubPeriod:
    """A stretch of a billing period over which one component's rate is constant."""

    start: date
    end: date
    days: int


def sub_periods(
    start: date, end: date, change_dates: Sequence[date]
) -> list[SubPeriod]:
    """Cut [start, end] at each change date, which opens a new sub-period."""
    edges = [start, *change_dates]
    out = []
    for i, begins in enumerate(edges):
        finishes = edges[i + 1] - timedelta(days=1) if i + 1 < len(edges) else end
        out.append(SubPeriod(begins, finishes, (finishes - begins).days + 1))
    return out


def allocate(consumption: Decimal, subs: Sequence[SubPeriod]) -> list[Decimal]:
    """Split consumption between sub-periods, pro rata by days.

    Each share but the last is rounded to whole m³, which is what the invoice
    lines show; the last takes the remainder so the shares always sum to the
    metered consumption. Rounding every share independently can overshoot — 7 m³
    over 15 + 15 days would give 4 + 4.
    """
    total_days = sum(s.days for s in subs)
    shares: list[Decimal] = []
    used = Decimal(0)
    for s in subs[:-1]:
        share = (consumption * s.days / total_days).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        shares.append(share)
        used += share
    shares.append(consumption - used)
    return shares


def tier_limits(days: int, bounds: tuple[int, ...]) -> list[int]:
    """Prorate the per-30-day tier limits to the elapsed days, rounded half up."""
    return [
        int((Decimal(b) * days / _THIRTY).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        for b in bounds
    ]


# The CIVA art. 2 nº2 exemption, encoded once, on the line.
VAT_EXEMPT = frozenset({"waste_variable", "waste_fixed", "tax_waste_mgmt"})

# Which config toggle governs which component prefix.
_TOGGLE = (
    ("include_water", "water"),
    ("include_sanitation", "sanitation"),
    ("include_waste", "waste"),
    ("include_taxes", "tax"),
)


@dataclass(frozen=True)
class Line:
    """One invoice line. `start`/`end` are None on charges billed per period."""

    component: str
    start: date | None
    end: date | None
    qty: Decimal
    rate: Decimal
    value: Decimal
    vat: bool


@dataclass
class Breakdown:
    water: Decimal
    sanitation: Decimal
    waste: Decimal
    taxes: Decimal
    base_without_vat: Decimal
    vat: Decimal
    total: Decimal
    lines: list[Line]


def _enabled(component: str, config: CalcConfig) -> bool:
    for toggle, prefix in _TOGGLE:
        if component.startswith(prefix):
            return getattr(config, toggle)
    return True


def _water_lines(
    subs: list[SubPeriod], shares: list[Decimal], schedule: TariffSchedule
) -> list[Line]:
    """Tiers per sub-period: limits reprorated to its days, restarting at zero.

    Each sub-period is billed as if it were a period of its own — that is what
    the invoices show, with the second half of a split period back on tier 1.
    """
    lines: list[Line] = []
    for sub, share in zip(subs, shares):
        tariff = schedule.at(sub.start)
        limits = tier_limits(sub.days, tariff.water_tier_bounds)
        lowers = [Decimal(0)] + [Decimal(v) for v in limits]
        uppers = [Decimal(v) for v in limits] + [None]
        for index, (lower, upper, rate) in enumerate(
            zip(lowers, uppers, tariff.water_tier_prices)
        ):
            capped = share if upper is None else min(share, upper)
            qty = max(Decimal(0), capped - lower)
            if qty <= 0:
                continue
            if rate is None:
                effective = [
                    p.effective_from for p in schedule.periods
                    if p.effective_from <= sub.start
                ][-1]
                raise UnknownTariffValue(
                    f"no price known for water tier {index + 1} on "
                    f"{sub.start.isoformat()}: add it to the tariff effective "
                    f"from {effective.isoformat()} under Configure -> Tariffs"
                )
            lines.append(Line(
                component=f"water_tier_{index + 1}",
                start=sub.start, end=sub.end, qty=qty, rate=rate,
                value=money(qty * rate), vat=True,
            ))
    return lines


def _rate_lines(
    component: str, subs: list[SubPeriod], shares: list[Decimal],
    schedule: TariffSchedule,
) -> list[Line]:
    """One line per sub-period for a component billed per m³."""
    lines = []
    for sub, share in zip(subs, shares):
        rate = getattr(schedule.at(sub.start), component)
        lines.append(Line(
            component=component, start=sub.start, end=sub.end, qty=share,
            rate=rate, value=money(share * rate),
            vat=component not in VAT_EXEMPT,
        ))
    return lines


def _fixed_line(component: str, end: date, schedule: TariffSchedule) -> Line:
    """Billed once, at the tariff in force on the period's last day."""
    rate = getattr(schedule.at(end), component)
    return Line(
        component=component, start=None, end=None, qty=Decimal(1), rate=rate,
        value=money(rate), vat=component not in VAT_EXEMPT,
    )


def _split(
    component: str, start: date, end: date, consumption: Decimal,
    schedule: TariffSchedule,
) -> tuple[list[SubPeriod], list[Decimal]]:
    subs = sub_periods(start, end, schedule.change_dates_for(component, start, end))
    return subs, allocate(consumption, subs)


def calcular(
    start: date, end: date, consumption: Decimal, config: CalcConfig
) -> Breakdown:
    """The AGERE bill for the period [start, end] and its consumption.

    Every variable component is split at the dates its own rate changes, so a
    period crossing a tariff change can have water on one line and the resource
    taxes on two. Fixed charges are billed once, at the end-of-period tariff.
    """
    if end < start:
        raise ValueError(
            f"period end {end.isoformat()} must be on or after the start "
            f"{start.isoformat()}"
        )
    schedule = config.schedule
    lines: list[Line] = []

    for component in VARIABLE_COMPONENTS:
        if not _enabled(component, config):
            continue
        subs, shares = _split(component, start, end, consumption, schedule)
        if component == "water":
            lines.extend(_water_lines(subs, shares, schedule))
        else:
            lines.extend(_rate_lines(component, subs, shares, schedule))

    for component in FIXED_COMPONENTS:
        if _enabled(component, config):
            lines.append(_fixed_line(component, end, schedule))

    def group(prefix: str) -> Decimal:
        return sum(
            (l.value for l in lines if l.component.startswith(prefix)), Decimal(0)
        )

    base = sum((l.value for l in lines), Decimal(0))
    vat_base = sum((l.value for l in lines if l.vat), Decimal(0))
    vat = money(vat_base * config.vat_rate) if config.include_vat else Decimal(0)
    return Breakdown(
        water=group("water"), sanitation=group("sanitation"),
        waste=group("waste"), taxes=group("tax"),
        base_without_vat=money(base), vat=vat, total=money(base + vat),
        lines=lines,
    )


def marginal_price(
    start: date, end: date, consumption: Decimal, today: date, config: CalcConfig
) -> Decimal:
    """Cost of the next cubic metre (EUR/m³), at today's position in the period.

    The next m³ falls in the sub-period containing `today`, so it is that
    sub-period's tariff, day count and share that decide the tier. Still an
    approximation: it excludes the charges billed per period.
    """
    schedule = config.schedule
    subs, shares = _split("water", start, end, consumption, schedule)
    index = next(
        (i for i, s in enumerate(subs) if s.start <= today <= s.end), len(subs) - 1
    )
    sub, share = subs[index], shares[index]
    tariff = schedule.at(sub.start)
    limits = tier_limits(sub.days, tariff.water_tier_bounds)

    tier = len(tariff.water_tier_prices) - 1
    for i, limit in enumerate(limits):
        if share < Decimal(limit):
            tier = i
            break

    subject = Decimal(0)
    vat_free = Decimal(0)
    if config.include_water:
        rate = tariff.water_tier_prices[tier]
        if rate is None:
            raise UnknownTariffValue(
                f"no price known for water tier {tier + 1} on {today.isoformat()}"
            )
        subject += rate
    if config.include_sanitation:
        subject += schedule.at(today).sanitation_drainage
    if config.include_taxes:
        t = schedule.at(today)
        subject += t.tax_water + t.tax_sanitation
    if config.include_waste:
        vat_free += schedule.at(today).waste_variable

    if config.include_vat:
        subject = subject * (Decimal(1) + config.vat_rate)
    return price4(subject + vat_free)
