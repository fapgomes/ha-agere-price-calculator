"""Pure consumption projection for the period in progress. No Home Assistant deps.

Projecting from the current period's rate alone is unusable in the first days: on
day 1 a single heavy day dominates. Projecting from history alone ignores what is
actually happening. So the two are blended, weighted by how far into the period
we are — history at the start, the real rate at the end, with a smooth transition
and no thresholds.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from .readings import Cycle

_MILLI = Decimal("0.001")


def _historical_daily(closed: Sequence[Cycle]) -> Decimal | None:
    """Average m³/day across every closed period, or None without history.

    Totals rather than a mean of per-period rates: a 63-day period should weigh
    more than a 28-day one.
    """
    days = sum(c.days for c in closed)
    if not days:
        return None
    return sum((c.consumption for c in closed), Decimal(0)) / Decimal(days)


def project_consumption(
    current: Cycle, days_elapsed: int, closed: Sequence[Cycle]
) -> Decimal:
    """Projected consumption (m³) for the whole of `current`, rounded to litres."""
    metered = Decimal(current.consumption)
    historical = _historical_daily(closed)

    current_daily = metered / Decimal(days_elapsed) if days_elapsed > 0 else None
    if current_daily is None and historical is None:
        return metered.quantize(_MILLI)
    if historical is None:
        daily = current_daily
    elif current_daily is None:
        daily = historical
    else:
        weight = min(Decimal(days_elapsed) / Decimal(current.days), Decimal(1))
        daily = weight * current_daily + (Decimal(1) - weight) * historical

    projected = daily * Decimal(current.days)
    # Never forecast less than the meter already shows.
    return max(projected, metered).quantize(_MILLI)
