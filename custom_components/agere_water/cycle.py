"""Pure billing-cycle tracking for the AGERE Water Price integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class CycleState:
    cycle_start: date
    baseline: Decimal


def last_reset_on_or_before(day: date, reset_day: int) -> date:
    """Most recent reset boundary (day == reset_day) on or before `day`."""
    if day.day >= reset_day:
        return date(day.year, day.month, reset_day)
    # step back one month
    year = day.year if day.month > 1 else day.year - 1
    month = day.month - 1 if day.month > 1 else 12
    return date(year, month, reset_day)


class CycleManager:
    def __init__(self, reset_day: int, state: CycleState | None = None) -> None:
        self._reset_day = reset_day
        self._state = state

    @property
    def state(self) -> CycleState | None:
        return self._state

    def update(self, now: date, meter_total: Decimal) -> CycleState:
        boundary = last_reset_on_or_before(now, self._reset_day)
        if self._state is None or boundary > self._state.cycle_start:
            self._state = CycleState(cycle_start=boundary, baseline=Decimal(meter_total))
        return self._state

    def consumption(self, meter_total: Decimal) -> Decimal:
        if self._state is None:
            return Decimal(0)
        return max(Decimal(0), Decimal(meter_total) - self._state.baseline)

    def days_elapsed(self, now: date) -> int:
        if self._state is None:
            return 1
        return max(1, (now - self._state.cycle_start).days + 1)
