"""Pure meter-reading log and billing-cycle derivation. No Home Assistant deps.

AGERE bills between meter-reading dates, not on a fixed day of the month: the
period runs from the day after the previous reading up to and including the
current reading date. Observed lengths on real invoices: 28, 33 and ~22 days.
Everything the integration shows is derived from the reading log.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"

# Used for the very first cycle, when there is no previous cycle to learn from.
DEFAULT_CYCLE_DAYS = 30


@dataclass(frozen=True)
class Reading:
    """One meter reading: the end of a billing period, and the meter value."""

    date: date
    m3: Decimal
    source: str = SOURCE_MANUAL


@dataclass(frozen=True)
class Cycle:
    """A billing period derived from the log. `days` feeds tier proration."""

    start: date
    end: date
    days: int
    consumption: Decimal
    estimated: bool


class ReadingLog:
    """An ordered, validated set of readings. Immutable: mutators return a new log."""

    def __init__(self, readings: Iterable[Reading] = ()) -> None:
        self._readings = sorted(readings, key=lambda r: r.date)
        self._validate()

    def _validate(self) -> None:
        prev: Reading | None = None
        for r in self._readings:
            if r.m3 < 0:
                raise ValueError(f"reading for {r.date.isoformat()} is negative")
            if prev is not None:
                if r.date == prev.date:
                    raise ValueError(f"duplicate reading date {r.date.isoformat()}")
                if r.m3 < prev.m3:
                    raise ValueError(
                        f"reading for {r.date.isoformat()} ({r.m3} m³) is lower "
                        f"than the previous reading for {prev.date.isoformat()} "
                        f"({prev.m3} m³)"
                    )
            prev = r

    def __len__(self) -> int:
        return len(self._readings)

    @property
    def readings(self) -> list[Reading]:
        return list(self._readings)

    @property
    def last(self) -> Reading | None:
        return self._readings[-1] if self._readings else None

    def set(self, reading: Reading) -> ReadingLog:
        """Insert `reading`, replacing any existing reading with the same date."""
        kept = [r for r in self._readings if r.date != reading.date]
        return ReadingLog([*kept, reading])

    def remove(self, when: date) -> ReadingLog:
        kept = [r for r in self._readings if r.date != when]
        if len(kept) == len(self._readings):
            raise ValueError(f"no reading for {when.isoformat()}")
        return ReadingLog(kept)

    def closed_cycles(self) -> list[Cycle]:
        """One cycle per consecutive pair of readings, oldest first."""
        return [
            Cycle(
                start=prev.date + timedelta(days=1),
                end=cur.date,
                days=(cur.date - prev.date).days,
                consumption=cur.m3 - prev.m3,
                estimated=False,
            )
            for prev, cur in zip(self._readings, self._readings[1:])
        ]

    def current_cycle(
        self, meter_total: Decimal, next_reading_date: date | None = None
    ) -> Cycle | None:
        """The in-progress cycle: starts the day after the last reading.

        Its length comes from `next_reading_date` when known (AGERE prints the
        next reading window on the invoice), otherwise it is estimated from the
        previous cycle. The length is fixed for the whole cycle so the
        accumulated cost stays monotonic.
        """
        last = self.last
        if last is None:
            return None
        start = last.date + timedelta(days=1)
        if next_reading_date is not None:
            if next_reading_date < start:
                raise ValueError(
                    f"next reading date {next_reading_date.isoformat()} must be "
                    f"after the last reading {last.date.isoformat()}"
                )
            end = next_reading_date
            estimated = False
        else:
            closed = self.closed_cycles()
            length = closed[-1].days if closed else DEFAULT_CYCLE_DAYS
            end = start + timedelta(days=length - 1)
            estimated = True
        return Cycle(
            start=start,
            end=end,
            days=(end - start).days + 1,
            consumption=max(Decimal(0), Decimal(meter_total) - last.m3),
            estimated=estimated,
        )


def days_elapsed(cycle: Cycle, today: date) -> int:
    """Days into the cycle, 1-based, clamped to the cycle length."""
    return max(1, min((today - cycle.start).days + 1, cycle.days))


def is_overdue(cycle: Cycle, today: date) -> bool:
    """True when the cycle should already have closed but no reading arrived."""
    return today > cycle.end
