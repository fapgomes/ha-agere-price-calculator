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
    current: Cycle, elapsed_days: Decimal | int, closed: Sequence[Cycle]
) -> Decimal:
    """Projected consumption (m³) for the whole of `current`, rounded to litres.

    Blending the period's own rate with the historical average, weighted by how
    far in we are, simplifies: the two `elapsed` terms cancel and what is left is
    what the meter already shows plus history for the days that remain.

        weight × (metered / elapsed) + (1 − weight) × historical, all × days
        = metered + (days − elapsed) × historical

    `elapsed_days` must be continuous, not a whole-day count. With an integer the
    remaining days drop by one the instant the date changes, and the projection
    falls by a full day of historical consumption in a single step.
    """
    metered = Decimal(current.consumption)
    days = Decimal(current.days)
    elapsed = min(max(Decimal(elapsed_days), Decimal(0)), days)
    historical = _historical_daily(closed)

    if historical is not None:
        projected = metered + (days - elapsed) * historical
    elif elapsed > 0:
        projected = metered * days / elapsed
    else:
        projected = metered

    # Never forecast less than the meter already shows.
    return max(projected, metered).quantize(_MILLI)
