"""Tariff values over time. No Home Assistant deps.

AGERE changes its tariff on dates that do not line up with the calendar year
and differ between components: the resource taxes moved on 2025-01-01, the
waste-management tax on 2026-01-01, and water and sanitation on 2026-02-01.
A table keyed by year would be wrong by construction.

Each snapshot holds the COMPLETE set of values, not a delta. That is what lets
one flat list express per-component effective dates: the 2025-01-01 entry is
the base with two taxes replaced.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal


class UnknownTariffValue(ValueError):
    """A value needed for this calculation was never billed, so it is not known."""


@dataclass(frozen=True)
class Tariff:
    """One complete set of AGERE Doméstico values.

    A `water_tier_prices` entry may be None, meaning the price is not known: a
    user adding an older tariff can leave one empty rather than guess it, and
    the calculation then refuses that period instead of undercharging.
    """

    water_tier_bounds: tuple[int, ...]
    water_tier_prices: tuple[Decimal | None, ...]
    water_availability: Decimal
    sanitation_drainage: Decimal
    sanitation_availability: Decimal
    waste_variable: Decimal
    waste_fixed: Decimal
    tax_water: Decimal
    tax_sanitation: Decimal
    tax_waste_mgmt: Decimal


@dataclass(frozen=True)
class TariffPeriod:
    effective_from: date
    tariff: Tariff


# Components billed per m³, each splitting at its own change dates. "water"
# covers the whole tier structure (bounds and prices together).
VARIABLE_COMPONENTS = (
    "water", "sanitation_drainage", "waste_variable", "tax_water",
    "tax_sanitation",
)
# Components billed once per period, at the tariff in force on the last day.
FIXED_COMPONENTS = (
    "water_availability", "sanitation_availability", "waste_fixed",
    "tax_waste_mgmt",
)


def _component_key(tariff: Tariff, component: str):
    if component == "water":
        return (tariff.water_tier_bounds, tariff.water_tier_prices)
    return getattr(tariff, component)


class TariffSchedule:
    """Tariffs by effective date. Immutable: mutators return a new schedule."""

    def __init__(self, periods: Iterable[TariffPeriod]) -> None:
        self._periods = sorted(periods, key=lambda p: p.effective_from)
        self._validate()

    def _validate(self) -> None:
        if not self._periods:
            raise ValueError("a tariff schedule needs at least one entry")
        seen: set[date] = set()
        for p in self._periods:
            if p.effective_from in seen:
                raise ValueError(
                    f"duplicate effective date {p.effective_from.isoformat()}"
                )
            seen.add(p.effective_from)
            self._validate_tariff(p)

    @staticmethod
    def _validate_tariff(p: TariffPeriod) -> None:
        t = p.tariff
        when = p.effective_from.isoformat()
        if len(t.water_tier_bounds) != len(t.water_tier_prices) - 1:
            raise ValueError(
                f"tariff {when}: needs one fewer tier bound than tier prices "
                f"({len(t.water_tier_bounds)} bounds, "
                f"{len(t.water_tier_prices)} prices)"
            )
        if any(b <= a for a, b in zip(t.water_tier_bounds, t.water_tier_bounds[1:])):
            raise ValueError(f"tariff {when}: tier bounds must be increasing")
        values = [v for v in t.water_tier_prices if v is not None]
        values += [
            getattr(t, name)
            for name in (*FIXED_COMPONENTS, "sanitation_drainage", "waste_variable",
                         "tax_water", "tax_sanitation")
        ]
        if any(v < 0 for v in values):
            raise ValueError(f"tariff {when}: values must not be negative")

    def __len__(self) -> int:
        return len(self._periods)

    @property
    def periods(self) -> list[TariffPeriod]:
        return list(self._periods)

    @property
    def earliest(self) -> date:
        return self._periods[0].effective_from

    @property
    def latest(self) -> date:
        return self._periods[-1].effective_from

    def at(self, day: date) -> Tariff:
        if day < self.earliest:
            raise ValueError(
                f"no tariff known for {day.isoformat()}: the earliest one starts "
                f"on {self.earliest.isoformat()}"
            )
        return [p.tariff for p in self._periods if p.effective_from <= day][-1]

    def change_dates_for(self, component: str, start: date, end: date) -> list[date]:
        """Dates in (start, end] where this component's value actually changes.

        A change on `start` needs no split — the whole period is already on the
        new value. The filter on the value itself is what keeps water on a
        single line when only the taxes moved.
        """
        out = []
        for p in self._periods:
            day = p.effective_from
            if not (start < day <= end):
                continue
            if _component_key(self.at(day), component) != _component_key(
                self.at(day - timedelta(days=1)), component
            ):
                out.append(day)
        return out

    def set(self, period: TariffPeriod) -> TariffSchedule:
        kept = [p for p in self._periods if p.effective_from != period.effective_from]
        return TariffSchedule([*kept, period])

    def remove(self, when: date) -> TariffSchedule:
        kept = [p for p in self._periods if p.effective_from != when]
        if len(kept) == len(self._periods):
            raise ValueError(f"no tariff with effective date {when.isoformat()}")
        return TariffSchedule(kept)

    def merge_newer(
        self, builtin: TariffSchedule, seeded_through: date | None
    ) -> tuple[TariffSchedule, date]:
        """Add built-in snapshots newer than the mark; touch nothing stored.

        The mark, rather than the newest stored date, is what makes a deletion
        stick: comparing against what is stored would bring back an entry the
        user removed.
        """
        floor = seeded_through
        stored_dates = {q.effective_from for q in self._periods}
        added = [
            p for p in builtin.periods
            if (floor is None or p.effective_from > floor)
            and p.effective_from not in stored_dates
        ]
        merged = TariffSchedule([*self._periods, *added]) if added else self
        return merged, max(builtin.latest, floor or builtin.latest)


# --- the built-in schedule, reconstructed from 20 real Doméstico invoices ---
# `replace` chains express copy-forward: each entry lists only what moved.

_BASE = Tariff(
    water_tier_bounds=(5, 10, 15, 25),
    water_tier_prices=(
        Decimal("0.4751"), Decimal("0.6206"), Decimal("0.8048"),
        Decimal("1.7550"),
        # Absent from every invoice on hand — no period reached it. Taken from
        # AGERE's published 2025 tariff sheet instead of guessed.
        Decimal("2.5114"),
    ),
    water_availability=Decimal("4.5476"),
    sanitation_drainage=Decimal("0.4402"),
    sanitation_availability=Decimal("4.4635"),
    waste_variable=Decimal("0.0136"),
    waste_fixed=Decimal("2.3310"),
    tax_water=Decimal("0.0379"),
    tax_sanitation=Decimal("0.0141"),
    tax_waste_mgmt=Decimal("2.4260"),
)
_TAXES_2025 = replace(
    _BASE, tax_water=Decimal("0.0382"), tax_sanitation=Decimal("0.0150")
)
_WASTE_TAX_2026 = replace(_TAXES_2025, tax_waste_mgmt=Decimal("2.8821"))
_TARIFF_2026 = replace(
    _WASTE_TAX_2026,
    water_tier_prices=(
        Decimal("0.5080"), Decimal("0.6636"), Decimal("0.8605"),
        Decimal("1.8765"), Decimal("2.6852"),
    ),
    water_availability=Decimal("4.8623"),
    sanitation_drainage=Decimal("0.4809"),
    sanitation_availability=Decimal("4.8766"),
    waste_variable=Decimal("0.0147"),
    waste_fixed=Decimal("2.5257"),
)

BUILTIN_SCHEDULE = TariffSchedule([
    # Earliest date with evidence, not the beginning of time: `at()` refuses
    # anything before it rather than applying these prices to older invoices.
    TariffPeriod(date(2024, 12, 12), _BASE),
    TariffPeriod(date(2025, 1, 1), _TAXES_2025),
    TariffPeriod(date(2026, 1, 1), _WASTE_TAX_2026),
    TariffPeriod(date(2026, 2, 1), _TARIFF_2026),
])
