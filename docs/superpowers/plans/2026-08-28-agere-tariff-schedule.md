# AGERE Tariff Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single hard-coded tariff with a schedule of tariffs keyed by effective date, and teach the engine to split a billing period per component when it crosses a change.

**Architecture:** A new pure `tariffs.py` owns `Tariff`, `TariffSchedule` and the built-in schedule reconstructed from real invoices. `calculator.py` is restructured around components: each variable component splits at the dates its own rate changes, allocates consumption pro rata by days, and (for water) reprorates and restarts the tiers per sub-period; fixed components are billed once at the period-end tariff. The schedule is seeded into `entry.options` and editable from the options flow.

**Tech Stack:** Python 3.13, Home Assistant custom integration, `voluptuous`, `homeassistant.helpers.selector`, pytest + `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-08-28-agere-tariff-schedule-design.md`

## Global Constraints

- **`tariffs.py` and `calculator.py` must not import `homeassistant`.** Same rule as `readings.py` and `forecast.py`: the engine tests are synchronous and run without the HA harness.
- **Money and volume are `Decimal` end to end.** Stored in options as strings; `null` means "unknown".
- **Rounding is line by line to cents, half up.** Subtotals sum already-rounded cents. This is what the invoices show and it is already implemented by `money()`.
- **Tier-limit proration stays `ROUND_HALF_UP` on `limit × days / 30`.** 18 of 19 real invoices are exact with it; a single 32-day invoice requires `ceil`, which would break three 28-day invoices. Do not "fix" this — Task 3 pins it with a test that explains why.
- **Never invent a tariff value.** An unknown price raises `UnknownTariffValue`; a date before the earliest snapshot raises `ValueError`. Both name what is missing and where to supply it.
- **Continuity requirement:** for a period that crosses no change date, the new engine must produce exactly what the current one produces. The two published invoice fixtures (28 m³/30 days → 71.21 €, 18 m³/28 days → 44.21 €) must keep passing.
- **Test layout:** engine tests in `tests/`, Home Assistant tests in `tests/ha/`. Run the full suite with `.venv/bin/python -m pytest -q` (Python 3.13, harness installed); `python3 -m pytest -q` on the system Python 3.14 runs the engine tests only and skips `tests/ha/`.
- **`strings.json` and `translations/en.json` are byte-identical.** Apply every string change to both.
- **No migration and no `ConfigFlow.VERSION` bump.** No option is removed or
  renamed; the seeding in `async_setup_entry` serves new and existing entries
  alike. Bumping the version would be work and risk with no return.
- **Real invoice data never enters the repository.** `docs/agere-invoices.local.md` is git-ignored and holds the 19-invoice extract for local validation. Test fixtures are synthetic except the two already-published figures.

---

### Task 1: `tariffs.py` — the schedule and the built-in data

**Files:**
- Create: `custom_components/agere_water/tariffs.py`
- Create: `tests/test_tariffs.py`
- Modify: `custom_components/agere_water/const.py` (remove `Tariff`, `DEFAULT_TARIFF`; re-point `CalcConfig`)

**Interfaces:**
- Consumes: nothing (pure module, stdlib only).
- Produces:
  - `class UnknownTariffValue(ValueError)`
  - `Tariff(water_tier_bounds, water_tier_prices, water_availability, sanitation_drainage, sanitation_availability, waste_variable, waste_fixed, tax_water, tax_sanitation, tax_waste_mgmt)` — frozen dataclass; `water_tier_prices` is `tuple[Decimal | None, ...]`
  - `TariffPeriod(effective_from: date, tariff: Tariff)` — frozen dataclass
  - `TariffSchedule(periods: Iterable[TariffPeriod])` — validates in `__init__`
  - `TariffSchedule.periods -> list[TariffPeriod]` (ascending), `TariffSchedule.earliest -> date`, `TariffSchedule.latest -> date`, `len(schedule)`
  - `TariffSchedule.at(day: date) -> Tariff` (raises `ValueError` before `earliest`)
  - `TariffSchedule.change_dates_for(component: str, start: date, end: date) -> list[date]`
  - `TariffSchedule.set(period: TariffPeriod) -> TariffSchedule` (upsert by date), `TariffSchedule.remove(when: date) -> TariffSchedule`
  - `TariffSchedule.merge_newer(builtin: TariffSchedule, seeded_through: date | None) -> tuple[TariffSchedule, date]`
  - `BUILTIN_SCHEDULE: TariffSchedule`
  - `VARIABLE_COMPONENTS: tuple[str, ...]` and `FIXED_COMPONENTS: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tariffs.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from custom_components.agere_water.tariffs import (
    BUILTIN_SCHEDULE, FIXED_COMPONENTS, VARIABLE_COMPONENTS, Tariff,
    TariffPeriod, TariffSchedule,
)


def test_builtin_has_the_four_known_effective_dates():
    assert [p.effective_from for p in BUILTIN_SCHEDULE.periods] == [
        date(2024, 12, 12), date(2025, 1, 1), date(2026, 1, 1), date(2026, 2, 1),
    ]


def test_builtin_snapshots_carry_the_full_value_set_forward():
    """Each snapshot is complete, not a delta: 2025-01-01 changes only the two
    resource taxes and must keep every other value from the base."""
    base = BUILTIN_SCHEDULE.at(date(2024, 12, 12))
    taxes = BUILTIN_SCHEDULE.at(date(2025, 1, 1))
    assert taxes.tax_water == Decimal("0.0382")
    assert taxes.tax_sanitation == Decimal("0.0150")
    assert taxes.water_tier_prices == base.water_tier_prices
    assert taxes.water_availability == base.water_availability
    assert taxes.tax_waste_mgmt == base.tax_waste_mgmt


def test_builtin_2026_02_01_is_the_current_tariff():
    t = BUILTIN_SCHEDULE.at(date(2026, 8, 12))
    assert t.water_tier_prices == (
        Decimal("0.5080"), Decimal("0.6636"), Decimal("0.8605"),
        Decimal("1.8765"), Decimal("2.6852"),
    )
    assert t.water_availability == Decimal("4.8623")
    assert t.sanitation_drainage == Decimal("0.4809")
    assert t.sanitation_availability == Decimal("4.8766")
    assert t.waste_variable == Decimal("0.0147")
    assert t.waste_fixed == Decimal("2.5257")
    assert t.tax_waste_mgmt == Decimal("2.8821")


def test_builtin_leaves_the_top_water_tier_unknown_before_2026_02():
    """The >25 m3 tier was never billed before 2026-02-01, so it has no value.
    Inventing one would silently undercharge by 43%."""
    assert BUILTIN_SCHEDULE.at(date(2025, 6, 1)).water_tier_prices[4] is None
    assert BUILTIN_SCHEDULE.at(date(2026, 2, 1)).water_tier_prices[4] is not None


def test_at_picks_the_tariff_in_force():
    assert BUILTIN_SCHEDULE.at(date(2025, 12, 31)).tax_waste_mgmt == Decimal("2.4260")
    assert BUILTIN_SCHEDULE.at(date(2026, 1, 1)).tax_waste_mgmt == Decimal("2.8821")
    assert BUILTIN_SCHEDULE.at(date(2026, 1, 31)).water_availability == Decimal("4.5476")
    assert BUILTIN_SCHEDULE.at(date(2026, 2, 1)).water_availability == Decimal("4.8623")


def test_at_refuses_dates_before_the_earliest_snapshot():
    """Applying 2024 prices to a 2023 invoice would look plausible and be wrong."""
    with pytest.raises(ValueError, match="2024-12-12"):
        BUILTIN_SCHEDULE.at(date(2023, 5, 1))


def test_change_dates_for_water_ignores_the_tax_only_change():
    """This is the whole mechanism: on 2025-01-01 only the taxes moved, so water
    must stay a single line over the full period."""
    assert BUILTIN_SCHEDULE.change_dates_for(
        "water", date(2024, 12, 20), date(2025, 1, 15)
    ) == []
    assert BUILTIN_SCHEDULE.change_dates_for(
        "tax_water", date(2024, 12, 20), date(2025, 1, 15)
    ) == [date(2025, 1, 1)]


def test_change_dates_for_water_sees_the_2026_02_change():
    assert BUILTIN_SCHEDULE.change_dates_for(
        "water", date(2026, 1, 20), date(2026, 2, 15)
    ) == [date(2026, 2, 1)]
    assert BUILTIN_SCHEDULE.change_dates_for(
        "tax_water", date(2026, 1, 20), date(2026, 2, 15)
    ) == []


def test_change_dates_for_excludes_the_period_start():
    """A change landing exactly on the first day needs no split: the whole period
    is already on the new tariff."""
    assert BUILTIN_SCHEDULE.change_dates_for(
        "water", date(2026, 2, 1), date(2026, 2, 28)
    ) == []


def test_change_dates_for_includes_the_period_end():
    assert BUILTIN_SCHEDULE.change_dates_for(
        "tax_waste_mgmt", date(2025, 12, 20), date(2026, 1, 1)
    ) == [date(2026, 1, 1)]


def test_component_groups_cover_every_tariff_field():
    fields = set(Tariff.__dataclass_fields__) - {"water_tier_bounds", "water_tier_prices"}
    assert set(VARIABLE_COMPONENTS) | set(FIXED_COMPONENTS) == fields | {"water"}
    assert not set(VARIABLE_COMPONENTS) & set(FIXED_COMPONENTS)


def _tariff(**kw):
    base = dict(
        water_tier_bounds=(5, 10, 15, 25),
        water_tier_prices=(Decimal("1"),) * 5,
        water_availability=Decimal("1"), sanitation_drainage=Decimal("1"),
        sanitation_availability=Decimal("1"), waste_variable=Decimal("1"),
        waste_fixed=Decimal("1"), tax_water=Decimal("1"),
        tax_sanitation=Decimal("1"), tax_waste_mgmt=Decimal("1"),
    )
    base.update(kw)
    return Tariff(**base)


def test_schedule_sorts_and_rejects_duplicate_dates():
    a = TariffPeriod(date(2026, 1, 1), _tariff())
    b = TariffPeriod(date(2025, 1, 1), _tariff())
    assert [p.effective_from for p in TariffSchedule([a, b]).periods] == [
        date(2025, 1, 1), date(2026, 1, 1),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        TariffSchedule([a, TariffPeriod(date(2026, 1, 1), _tariff())])


def test_schedule_rejects_an_empty_calendar():
    with pytest.raises(ValueError, match="at least one"):
        TariffSchedule([])


def test_schedule_rejects_negative_prices():
    with pytest.raises(ValueError, match="negative"):
        TariffSchedule([TariffPeriod(date(2026, 1, 1),
                                     _tariff(waste_fixed=Decimal("-1")))])


def test_schedule_rejects_bounds_that_do_not_increase():
    with pytest.raises(ValueError, match="increasing"):
        TariffSchedule([TariffPeriod(date(2026, 1, 1),
                                     _tariff(water_tier_bounds=(5, 5, 15, 25)))])


def test_schedule_rejects_bounds_of_the_wrong_length():
    with pytest.raises(ValueError, match="one fewer"):
        TariffSchedule([TariffPeriod(date(2026, 1, 1),
                                     _tariff(water_tier_bounds=(5, 10, 15)))])


def test_set_upserts_by_date_and_remove_drops():
    s = TariffSchedule([TariffPeriod(date(2026, 1, 1), _tariff())])
    s = s.set(TariffPeriod(date(2026, 1, 1), _tariff(waste_fixed=Decimal("2"))))
    assert len(s) == 1
    assert s.at(date(2026, 1, 1)).waste_fixed == Decimal("2")
    s = s.set(TariffPeriod(date(2026, 6, 1), _tariff()))
    assert len(s.remove(date(2026, 6, 1))) == 1
    with pytest.raises(ValueError, match="no tariff"):
        s.remove(date(2020, 1, 1))


def test_merge_newer_adds_only_dates_after_the_mark():
    stored = TariffSchedule([TariffPeriod(date(2024, 12, 12), _tariff())])
    merged, mark = stored.merge_newer(BUILTIN_SCHEDULE, date(2025, 1, 1))
    assert [p.effective_from for p in merged.periods] == [
        date(2024, 12, 12), date(2026, 1, 1), date(2026, 2, 1),
    ]
    assert mark == date(2026, 2, 1)


def test_merge_newer_never_overwrites_a_stored_snapshot():
    edited = TariffSchedule([TariffPeriod(date(2026, 2, 1),
                                          _tariff(waste_fixed=Decimal("9")))])
    merged, _ = edited.merge_newer(BUILTIN_SCHEDULE, date(2026, 2, 1))
    assert merged.at(date(2026, 2, 1)).waste_fixed == Decimal("9")


def test_merge_newer_seeds_everything_when_unmarked():
    merged, mark = TariffSchedule([TariffPeriod(date(2026, 2, 1), _tariff())]) \
        .merge_newer(BUILTIN_SCHEDULE, None)
    assert len(merged) == 4
    assert mark == date(2026, 2, 1)
    # the stored 2026-02-01 wins over the built-in one
    assert merged.at(date(2026, 2, 1)).waste_fixed == Decimal("1")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tariffs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.agere_water.tariffs'`

- [ ] **Step 3: Write `tariffs.py`**

```python
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

    `water_tier_prices` entries may be None: the >25 m³ tier was never billed
    before 2026-02-01, and guessing it would silently undercharge.
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

    def change_dates_for(
        self, component: str, start: date, end: date
    ) -> list[date]:
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
        added = [
            p for p in builtin.periods
            if (floor is None or p.effective_from > floor)
            and p.effective_from not in {q.effective_from for q in self._periods}
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
        None,  # >25 m³ never billed before 2026-02-01
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tariffs.py -q`
Expected: PASS (19 tests)

- [ ] **Step 5: Re-point `const.py` at the schedule**

Delete the `Tariff` dataclass and `DEFAULT_TARIFF` from `const.py`, and change `CalcConfig`:

```python
from .tariffs import BUILTIN_SCHEDULE, TariffSchedule


@dataclass(frozen=True)
class CalcConfig:
    """Everything the calculator needs beyond the period and its consumption."""

    schedule: TariffSchedule = BUILTIN_SCHEDULE
    include_water: bool = True
    include_sanitation: bool = True
    include_waste: bool = True
    include_taxes: bool = True
    include_vat: bool = True
    vat_rate: Decimal = DEFAULT_VAT_RATE
```

Add the two new option keys next to `CONF_NEXT_READING_DATE`:

```python
CONF_TARIFFS = "tariffs"
CONF_TARIFFS_SEEDED_THROUGH = "tariffs_seeded_through"
```

`Decimal` stays imported for `DEFAULT_VAT_RATE`. Nothing else in `const.py` changes.

- [ ] **Step 6: Confirm the breakage is only where expected**

Run: `.venv/bin/python -m pytest -q`
Expected: `tests/test_tariffs.py` passes; `tests/test_calculator.py` fails on `DEFAULT_TARIFF` and `CalcConfig(tariff=...)`, and the sensor/service tests fail with it. Tasks 2 and 3 fix the calculator; Task 6 fixes the callers. Note the failing names — they are the checklist for those tasks.

- [ ] **Step 7: Commit**

```bash
git add custom_components/agere_water/tariffs.py custom_components/agere_water/const.py tests/test_tariffs.py
git commit -m "feat: add a tariff schedule keyed by effective date

AGERE's tariff changes on dates that differ per component and never on
the calendar year, so each snapshot holds the complete value set and the
schedule is queried per component."
```

---

### Task 2: Sub-periods and consumption allocation

The two pure helpers the split is built on. Separate from the line builders because they are worth reviewing on their own: the allocation rule is where a rounding mistake would silently change totals.

**Files:**
- Modify: `custom_components/agere_water/calculator.py` (add helpers; nothing removed yet)
- Create: `tests/test_subperiods.py`

**Interfaces:**
- Consumes: nothing from Task 1 (dates and Decimals only).
- Produces:
  - `SubPeriod(start: date, end: date, days: int)` — frozen dataclass
  - `sub_periods(start: date, end: date, change_dates: Sequence[date]) -> list[SubPeriod]`
  - `allocate(consumption: Decimal, subs: Sequence[SubPeriod]) -> list[Decimal]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subperiods.py`:

```python
from datetime import date
from decimal import Decimal

from custom_components.agere_water.calculator import (
    SubPeriod, allocate, sub_periods,
)


def test_no_change_dates_gives_one_sub_period():
    subs = sub_periods(date(2026, 3, 1), date(2026, 3, 30), [])
    assert subs == [SubPeriod(date(2026, 3, 1), date(2026, 3, 30), 30)]


def test_one_change_date_splits_the_day_before():
    subs = sub_periods(date(2026, 1, 20), date(2026, 2, 15), [date(2026, 2, 1)])
    assert subs == [
        SubPeriod(date(2026, 1, 20), date(2026, 1, 31), 12),
        SubPeriod(date(2026, 2, 1), date(2026, 2, 15), 15),
    ]
    assert sum(s.days for s in subs) == 27


def test_two_change_dates_give_three_sub_periods():
    subs = sub_periods(
        date(2025, 12, 20), date(2026, 2, 10),
        [date(2026, 1, 1), date(2026, 2, 1)],
    )
    assert [s.days for s in subs] == [12, 31, 10]
    assert sum(s.days for s in subs) == 53


def test_allocation_is_proportional_to_days():
    subs = sub_periods(date(2026, 1, 20), date(2026, 2, 15), [date(2026, 2, 1)])
    assert allocate(Decimal("15"), subs) == [Decimal("7"), Decimal("8")]


def test_allocation_preserves_the_total():
    """Rounding each share independently can overshoot: 7 m3 over 15 + 15 days
    would give 4 + 4. The last share takes the remainder instead."""
    subs = [
        SubPeriod(date(2026, 1, 1), date(2026, 1, 15), 15),
        SubPeriod(date(2026, 1, 16), date(2026, 1, 30), 15),
    ]
    shares = allocate(Decimal("7"), subs)
    assert shares == [Decimal("4"), Decimal("3")]
    assert sum(shares) == Decimal("7")


def test_allocation_of_a_single_sub_period_is_the_whole_consumption():
    subs = [SubPeriod(date(2026, 3, 1), date(2026, 3, 30), 30)]
    assert allocate(Decimal("20.5"), subs) == [Decimal("20.5")]


def test_allocation_keeps_the_fraction_on_the_last_share():
    """Only whole m3 go to the earlier shares, because that is what the invoice
    lines show; whatever is left, fraction included, lands on the last."""
    subs = sub_periods(date(2026, 1, 20), date(2026, 2, 15), [date(2026, 2, 1)])
    shares = allocate(Decimal("15.4"), subs)
    assert shares[0] == Decimal("7")
    assert sum(shares) == Decimal("15.4")


def test_allocation_of_zero():
    subs = sub_periods(date(2026, 1, 20), date(2026, 2, 15), [date(2026, 2, 1)])
    assert allocate(Decimal("0"), subs) == [Decimal("0"), Decimal("0")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_subperiods.py -q`
Expected: FAIL — `ImportError: cannot import name 'SubPeriod'`

- [ ] **Step 3: Add the helpers to `calculator.py`**

Add near the top, after the existing `money`/`price4` helpers:

```python
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta


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


def allocate(
    consumption: Decimal, subs: Sequence[SubPeriod]
) -> list[Decimal]:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_subperiods.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/calculator.py tests/test_subperiods.py
git commit -m "feat: add sub-period splitting and consumption allocation"
```

---

### Task 3: Rewrite `calculator.py` around components

The core of the work. `calcular` changes signature to take the period's dates, and the body becomes one function per component group.

**Files:**
- Modify: `custom_components/agere_water/calculator.py` (replaces `water_lines`, `calcular`, `marginal_price`, `TierLine`, `Breakdown`)
- Modify: `tests/test_calculator.py`

**Interfaces:**
- Consumes: `SubPeriod`, `sub_periods`, `allocate` (Task 2); `TariffSchedule`, `UnknownTariffValue`, `VARIABLE_COMPONENTS`, `FIXED_COMPONENTS` (Task 1); `CalcConfig` (`const.py`).
- Produces:
  - `Line(component: str, start: date | None, end: date | None, qty: Decimal, rate: Decimal, value: Decimal, vat: bool)` — frozen dataclass; `start`/`end` are `None` on fixed charges
  - `Breakdown(water, sanitation, waste, taxes, base_without_vat, vat, total, lines: list[Line])`
  - `calcular(start: date, end: date, consumption: Decimal, config: CalcConfig) -> Breakdown`
  - `marginal_price(start: date, end: date, consumption: Decimal, today: date, config: CalcConfig) -> Decimal`
  - `tier_limits(days: int, bounds: tuple[int, ...]) -> list[int]` — unchanged, still `ROUND_HALF_UP`
  - `VAT_EXEMPT: frozenset[str]` — components outside VAT

- [ ] **Step 1: Write the failing tests**

Replace the body of `tests/test_calculator.py` from `def test_default_tariff_values` onward, keeping the `money`/`price4`/`tier_limits`/`water_lines`-free helpers tests. The full new file:

```python
from datetime import date
from decimal import Decimal

import pytest

from custom_components.agere_water.calculator import (
    calcular, marginal_price, money, price4, tier_limits,
)
from custom_components.agere_water.const import CalcConfig
from custom_components.agere_water.tariffs import (
    BUILTIN_SCHEDULE, UnknownTariffValue,
)

CFG = CalcConfig()


def test_calc_config_defaults():
    assert CFG.include_vat is True
    assert CFG.vat_rate == Decimal("0.06")
    assert CFG.schedule is BUILTIN_SCHEDULE


def test_money_rounds_half_up():
    assert money(Decimal("3.7014")) == Decimal("3.70")
    assert money(Decimal("13.4652")) == Decimal("13.47")
    assert money(Decimal("2.6544")) == Decimal("2.65")


def test_price4_rounds_half_up():
    assert price4(Decimal("1.23455")) == Decimal("1.2346")
    assert price4(Decimal("0.50805")) == Decimal("0.5081")


def test_tier_limits_prorate_half_up():
    assert tier_limits(30, (5, 10, 15, 25)) == [5, 10, 15, 25]
    assert tier_limits(28, (5, 10, 15, 25)) == [5, 9, 14, 23]
    assert tier_limits(33, (5, 10, 15, 25)) == [6, 11, 17, 28]
    assert tier_limits(12, (5, 10, 15, 25)) == [2, 4, 6, 10]
    assert tier_limits(15, (5, 10, 15, 25)) == [3, 5, 8, 13]


def test_tier_limits_at_32_days_keep_round_not_ceil():
    """A real 32-day invoice used tier limit 6, i.e. ceil(5*32/30) = 6. Three
    independent 28-day invoices need round (10*28/30 = 9.333 -> 9), and ceil
    would give 10 there. No single rule fits both, so round stays and the
    32-day case is off by about 0.16 EUR. Do not "fix" this without a 31- or
    32-day invoice that settles it."""
    assert tier_limits(32, (5, 10, 15, 25)) == [5, 11, 16, 27]


# --- continuity: a period crossing no change must match the old engine ---

def test_published_invoice_28m3_30days():
    bd = calcular(date(2026, 5, 14), date(2026, 6, 12), Decimal("28"), CFG)
    assert bd.total == Decimal("71.21")


def test_published_invoice_18m3_28days():
    bd = calcular(date(2026, 6, 13), date(2026, 7, 10), Decimal("18"), CFG)
    assert bd.total == Decimal("44.21")


def test_period_without_a_change_has_one_line_per_component():
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), CFG)
    assert bd.water == Decimal("24.40")
    assert bd.sanitation == Decimal("14.50")
    assert bd.waste == Decimal("2.82")
    assert bd.taxes == Decimal("3.94")
    assert bd.total == Decimal("48.06")
    assert [l.component for l in bd.lines if l.component.startswith("water_tier")] == [
        "water_tier_1", "water_tier_2", "water_tier_3", "water_tier_4",
    ]
    assert all(l.start is None for l in bd.lines if l.component in (
        "water_availability", "sanitation_availability", "waste_fixed",
        "tax_waste_mgmt",
    ))


# --- the split, per component ---

def test_split_at_2026_02_01_restarts_the_water_tiers():
    """A: 7 m3 in 12 days, limits 2/4/6/10 -> 2+2+2+1 at the old prices.
    B: 8 m3 in 15 days, limits 3/5/8/13 -> 3+2+3 at the new prices, back to
    tier 1."""
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    water = [l for l in bd.lines if l.component.startswith("water_tier")]
    assert [(l.component, str(l.qty), str(l.rate), str(l.value)) for l in water] == [
        ("water_tier_1", "2", "0.4751", "0.95"),
        ("water_tier_2", "2", "0.6206", "1.24"),
        ("water_tier_3", "2", "0.8048", "1.61"),
        ("water_tier_4", "1", "1.7550", "1.76"),
        ("water_tier_1", "3", "0.5080", "1.52"),
        ("water_tier_2", "2", "0.6636", "1.33"),
        ("water_tier_3", "3", "0.8605", "2.58"),
    ]
    assert water[0].start == date(2026, 1, 20)
    assert water[0].end == date(2026, 1, 31)
    assert water[4].start == date(2026, 2, 1)
    assert water[4].end == date(2026, 2, 15)


def test_split_bills_the_fixed_charges_once_at_the_end_tariff():
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    fixed = {l.component: l for l in bd.lines if l.start is None}
    assert len(fixed) == 4
    assert fixed["water_availability"].rate == Decimal("4.8623")
    assert fixed["sanitation_availability"].rate == Decimal("4.8766")
    assert fixed["waste_fixed"].rate == Decimal("2.5257")
    assert fixed["tax_waste_mgmt"].rate == Decimal("2.8821")


def test_split_at_2026_02_01_totals():
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    assert bd.water == Decimal("15.85")
    assert bd.vat == Decimal("1.71")
    assert bd.total == Decimal("35.80")


def test_split_at_2025_01_01_touches_only_the_taxes():
    """This is the mechanism that a period-wide split would get wrong: on
    2025-01-01 only the resource taxes moved, so water stays a single line over
    all 27 days and the taxes split 7 + 8 m3."""
    bd = calcular(date(2024, 12, 20), date(2025, 1, 15), Decimal("15"), CFG)
    water = [l for l in bd.lines if l.component.startswith("water_tier")]
    assert len(water) == 4
    assert all(l.start == date(2024, 12, 20) for l in water)
    assert [str(l.qty) for l in water] == ["5", "4", "5", "1"]

    tax_water = [l for l in bd.lines if l.component == "tax_water"]
    assert [(str(l.qty), str(l.rate), str(l.value)) for l in tax_water] == [
        ("7", "0.0379", "0.27"),
        ("8", "0.0382", "0.31"),
    ]
    drainage = [l for l in bd.lines if l.component == "sanitation_drainage"]
    assert len(drainage) == 1
    assert bd.total == Decimal("33.63")


# --- structure and VAT ---

def test_subtotals_are_sums_of_their_lines():
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    for attr, prefix in (
        ("water", "water"), ("sanitation", "sanitation"),
        ("waste", "waste"), ("taxes", "tax"),
    ):
        assert getattr(bd, attr) == sum(
            (l.value for l in bd.lines if l.component.startswith(prefix)),
            Decimal(0),
        )
    assert bd.base_without_vat == sum((l.value for l in bd.lines), Decimal(0))
    assert bd.total == bd.base_without_vat + bd.vat


def test_vat_flag_encodes_the_civa_exemption():
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), CFG)
    exempt = {l.component for l in bd.lines if not l.vat}
    assert exempt == {"waste_variable", "waste_fixed", "tax_waste_mgmt"}
    assert bd.vat == money(
        sum((l.value for l in bd.lines if l.vat), Decimal(0)) * Decimal("0.06")
    )


def test_disabled_components_produce_no_lines():
    cfg = CalcConfig(include_sanitation=False, include_waste=False)
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), cfg)
    assert bd.sanitation == Decimal(0)
    assert bd.waste == Decimal(0)
    assert not [l for l in bd.lines if l.component.startswith(("sanitation", "waste"))]
    assert bd.water == Decimal("24.40")


def test_vat_can_be_switched_off():
    cfg = CalcConfig(include_vat=False)
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), cfg)
    assert bd.vat == Decimal(0)
    assert bd.total == bd.base_without_vat


# --- refusals ---

def test_unknown_tier_price_raises_and_names_the_tier():
    """The >25 m3 tier has no price before 2026-02-01. 30 m3 in 30 days reaches
    it, so the calculation must refuse instead of undercharging."""
    with pytest.raises(UnknownTariffValue, match="tier 5"):
        calcular(date(2025, 3, 1), date(2025, 3, 30), Decimal("30"), CFG)


def test_a_period_starting_before_the_earliest_tariff_raises():
    with pytest.raises(ValueError, match="2024-12-12"):
        calcular(date(2023, 1, 1), date(2023, 1, 30), Decimal("10"), CFG)


def test_end_before_start_raises():
    with pytest.raises(ValueError, match="after"):
        calcular(date(2026, 3, 30), date(2026, 3, 1), Decimal("10"), CFG)


# --- marginal price ---

def test_marginal_price_uses_the_tariff_in_force_today():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    # 12 m3 into a 30-day March period: limits 5/10/15/25, next m3 in tier 3
    assert marginal_price(
        date(2026, 3, 1), date(2026, 3, 30), Decimal("12"), date(2026, 3, 20), cfg
    ) == Decimal("0.8605")


def test_marginal_price_in_a_split_period_uses_todays_sub_period():
    """Sitting in the second sub-period, the next m3 costs the new price, and the
    tiers are those of that sub-period alone."""
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    got = marginal_price(
        date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), date(2026, 2, 10), cfg
    )
    assert got in (Decimal("0.5080"), Decimal("0.6636"), Decimal("0.8605"))


def test_marginal_price_full_with_vat():
    cfg = CalcConfig()
    # tier 1 0.5080 + drainage 0.4809 + taxes 0.0382 + 0.0150 = 1.0421
    # x 1.06 = 1.104626 ; + waste variable 0.0147 (no VAT) -> 1.1193
    assert marginal_price(
        date(2026, 3, 1), date(2026, 3, 30), Decimal("0"), date(2026, 3, 1), cfg
    ) == Decimal("1.1193")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_calculator.py -q`
Expected: FAIL — `calcular()` still takes `(consumo, days, config)`, so every call raises `TypeError`.

- [ ] **Step 3: Rewrite the body of `calculator.py`**

Keep the module docstring, `money`, `price4`, `tier_limits`, `SubPeriod`, `sub_periods` and `allocate`. Replace `TierLine`, `water_lines`, `Breakdown`, `calcular` and `marginal_price` with:

```python
from .const import CalcConfig
from .tariffs import (
    FIXED_COMPONENTS, VARIABLE_COMPONENTS, TariffSchedule, UnknownTariffValue,
)

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


def _fixed_line(
    component: str, end: date, schedule: TariffSchedule
) -> Line:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_calculator.py tests/test_subperiods.py tests/test_tariffs.py -q`
Expected: PASS. If `test_published_invoice_28m3_30days` fails, the restructuring changed behaviour for a non-crossing period — stop and find out why before going on; that test is the continuity anchor.

- [ ] **Step 5: Validate against the real invoices**

The 19 real invoices are in `docs/agere-invoices.local.md` (git-ignored). Write a throwaway script in the scratchpad that reads the table, calls `calcular(start, end, m3, CalcConfig())` for each row and prints the difference against the billed total.

Expected: 18 of 19 exact; the 32-day period off by +0.16 €. Report the result. Do not commit the script or the data.

- [ ] **Step 6: Commit**

```bash
git add custom_components/agere_water/calculator.py tests/test_calculator.py
git commit -m "feat: split billing periods per component at tariff changes

calcular now takes the period's dates and derives days from them, which
also removes the possibility of passing a day count that disagrees with
the dates.

Each variable component splits at the dates its own rate changes: a
period crossing 2025-01-01 has water on a single line and the resource
taxes on two, which is what the invoices show. Water tiers reprorate and
restart per sub-period. Fixed charges are billed once at the end-of-period
tariff. Subtotals are now sums of the lines, so they cannot drift from
them, and the VAT exemption lives on the line."
```

---

### Task 4: Tariff serialisation in `entry_options.py`

**Files:**
- Modify: `custom_components/agere_water/entry_options.py`
- Modify: `tests/test_entry_options.py`

**Interfaces:**
- Consumes: `Tariff`, `TariffPeriod`, `TariffSchedule`, `BUILTIN_SCHEDULE` (Task 1); `CONF_TARIFFS`, `CONF_TARIFFS_SEEDED_THROUGH` (Task 1).
- Produces:
  - `tariffs_from_options(options) -> TariffSchedule` (falls back to `BUILTIN_SCHEDULE` when absent; raises `ValueError` on malformed data)
  - `tariffs_to_options(schedule) -> list[dict]`
  - `seeded_through_from_options(options) -> date | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_entry_options.py`:

```python
from custom_components.agere_water.const import (
    CONF_TARIFFS, CONF_TARIFFS_SEEDED_THROUGH,
)
from custom_components.agere_water.entry_options import (
    seeded_through_from_options, tariffs_from_options, tariffs_to_options,
)
from custom_components.agere_water.tariffs import BUILTIN_SCHEDULE

STORED_TARIFF = {
    "effective_from": "2026-02-01",
    "water_tier_bounds": [5, 10, 15, 25],
    "water_tier_prices": ["0.5080", "0.6636", "0.8605", "1.8765", "2.6852"],
    "water_availability": "4.8623",
    "sanitation_drainage": "0.4809",
    "sanitation_availability": "4.8766",
    "waste_variable": "0.0147",
    "waste_fixed": "2.5257",
    "tax_water": "0.0382",
    "tax_sanitation": "0.0150",
    "tax_waste_mgmt": "2.8821",
}


def test_tariffs_from_options_falls_back_to_the_builtin():
    assert tariffs_from_options({}) is BUILTIN_SCHEDULE
    assert tariffs_from_options({CONF_TARIFFS: None}) is BUILTIN_SCHEDULE
    assert tariffs_from_options({CONF_TARIFFS: []}) is BUILTIN_SCHEDULE


def test_tariffs_roundtrip():
    schedule = tariffs_from_options({CONF_TARIFFS: [STORED_TARIFF]})
    assert len(schedule) == 1
    t = schedule.at(date(2026, 2, 1))
    assert t.water_tier_prices[0] == Decimal("0.5080")
    assert t.tax_waste_mgmt == Decimal("2.8821")
    assert tariffs_to_options(schedule) == [STORED_TARIFF]


def test_tariffs_roundtrip_the_builtin_schedule():
    stored = tariffs_to_options(BUILTIN_SCHEDULE)
    rebuilt = tariffs_from_options({CONF_TARIFFS: stored})
    assert len(rebuilt) == len(BUILTIN_SCHEDULE)
    for a, b in zip(rebuilt.periods, BUILTIN_SCHEDULE.periods):
        assert a == b


def test_unknown_tier_price_survives_as_null():
    stored = tariffs_to_options(BUILTIN_SCHEDULE)
    base = next(t for t in stored if t["effective_from"] == "2024-12-12")
    assert base["water_tier_prices"][4] is None
    rebuilt = tariffs_from_options({CONF_TARIFFS: stored})
    assert rebuilt.at(date(2025, 6, 1)).water_tier_prices[4] is None


def test_tariffs_from_options_rejects_a_malformed_date():
    bad = {**STORED_TARIFF, "effective_from": "01-02-2026"}
    with pytest.raises(ValueError, match="effective date"):
        tariffs_from_options({CONF_TARIFFS: [bad]})


def test_tariffs_from_options_rejects_a_malformed_value():
    bad = {**STORED_TARIFF, "waste_fixed": "not a number"}
    with pytest.raises(ValueError, match="waste_fixed"):
        tariffs_from_options({CONF_TARIFFS: [bad]})


def test_tariffs_from_options_rejects_a_missing_field():
    bad = {k: v for k, v in STORED_TARIFF.items() if k != "tax_water"}
    with pytest.raises(ValueError, match="tax_water"):
        tariffs_from_options({CONF_TARIFFS: [bad]})


def test_tariffs_to_options_drops_float_formatting():
    # same formatter as the readings path, renamed to format_decimal in this task
    """Values come back from the form as text but may have been typed as 4.80."""
    stored = tariffs_to_options(tariffs_from_options({CONF_TARIFFS: [
        {**STORED_TARIFF, "waste_fixed": "2.50"}
    ]}))
    assert stored[0]["waste_fixed"] == "2.5"


def test_seeded_through_from_options():
    assert seeded_through_from_options(
        {CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01"}
    ) == date(2026, 2, 1)
    assert seeded_through_from_options({}) is None
    assert seeded_through_from_options({CONF_TARIFFS_SEEDED_THROUGH: None}) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_entry_options.py -q`
Expected: FAIL — `ImportError: cannot import name 'tariffs_from_options'`

- [ ] **Step 3: Add the functions to `entry_options.py`**

```python
from .const import (
    CONF_NEXT_READING_DATE, CONF_READINGS, CONF_TARIFFS,
    CONF_TARIFFS_SEEDED_THROUGH,
)
from .tariffs import BUILTIN_SCHEDULE, Tariff, TariffPeriod, TariffSchedule

_TARIFF_VALUES = (
    "water_availability", "sanitation_drainage", "sanitation_availability",
    "waste_variable", "waste_fixed", "tax_water", "tax_sanitation",
    "tax_waste_mgmt",
)


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
                None if v is None else format_decimal(v) for v in t.water_tier_prices
            ],
        }
        for name in _TARIFF_VALUES:
            entry[name] = format_decimal(getattr(t, name))
        out.append(entry)
    return out


def seeded_through_from_options(options: Mapping[str, Any]) -> date | None:
    raw = options.get(CONF_TARIFFS_SEEDED_THROUGH)
    return date.fromisoformat(raw) if raw else None
```

`_format_m3` already exists in this module and does exactly the right thing for
prices too: shortest exact decimal form, no float noise, no exponents. **Rename it
to `format_decimal`** (public, no leading underscore) and update its two existing
callers in `readings_to_options`, plus its docstring, which currently talks only
about `m3`:

```python
def format_decimal(value: Decimal) -> str:
    """Shortest exact decimal form, without float noise or exponents.

    Values reach us as floats from the service schema and the number selector,
    so 543 arrives as Decimal("543.0") and would be stored — and shown — as
    "543.0". `normalize()` alone is not enough: it renders 500.0 as "5E+2".
    """
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return str(value.normalize())
```

The rename matters because Task 7 needs the same formatting to pre-fill the
tariff form. Two copies of this logic in two modules would drift.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_entry_options.py -q`
Expected: PASS

Note: `test_tariffs_roundtrip` compares against `STORED_TARIFF`, whose prices are
written as `"0.5080"`. `format_decimal` trims that to `"0.508"`. Fix the test's
expected dict to the trimmed forms rather than weakening `format_decimal` — the
trimmed form round-trips to the same Decimal, and consistency with the reading
path matters more than matching the invoice's printed zeros.

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/entry_options.py tests/test_entry_options.py
git commit -m "feat: store and load the tariff schedule from entry options"
```

---

### Task 5: Seed the schedule into the entry, adding only newer snapshots

**Files:**
- Modify: `custom_components/agere_water/__init__.py`
- Create: `tests/ha/test_tariff_seeding.py`

**Interfaces:**
- Consumes: `tariffs_from_options`, `tariffs_to_options`, `seeded_through_from_options` (Task 4); `BUILTIN_SCHEDULE`, `TariffSchedule` (Task 1); `CONF_TARIFFS`, `CONF_TARIFFS_SEEDED_THROUGH` (Task 1).
- Produces: seeding inside `async_setup_entry`, before the platforms load. No new public names.

- [ ] **Step 1: Write the failing tests**

Create `tests/ha/test_tariff_seeding.py`:

```python
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_READINGS, CONF_SOURCE, CONF_TARIFFS, CONF_TARIFFS_SEEDED_THROUGH,
    CONF_VAT_RATE, DOMAIN,
)

READINGS = [{"date": "2026-08-12", "m3": "536", "source": "manual"}]


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    hass.states.async_set("sensor.water_meter_total", "543",
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={CONF_READINGS: READINGS, CONF_VAT_RATE: "0.06", **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_seeds_the_builtin_schedule(hass: HomeAssistant):
    entry = await _setup(hass)
    stored = entry.options[CONF_TARIFFS]
    assert [t["effective_from"] for t in stored] == [
        "2024-12-12", "2025-01-01", "2026-01-01", "2026-02-01",
    ]
    assert entry.options[CONF_TARIFFS_SEEDED_THROUGH] == "2026-02-01"


async def test_setup_does_not_rewrite_on_every_start(hass: HomeAssistant):
    """Writing options reloads the entry, so an unconditional write would loop."""
    entry = await _setup(hass)
    before = entry.options[CONF_TARIFFS]
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options[CONF_TARIFFS] == before


async def test_setup_keeps_an_edited_snapshot(hass: HomeAssistant):
    edited = [{
        "effective_from": "2026-02-01",
        "water_tier_bounds": [5, 10, 15, 25],
        "water_tier_prices": ["0.6", "0.7", "0.9", "1.9", "2.7"],
        "water_availability": "9.99", "sanitation_drainage": "0.5",
        "sanitation_availability": "5", "waste_variable": "0.02",
        "waste_fixed": "3", "tax_water": "0.04", "tax_sanitation": "0.02",
        "tax_waste_mgmt": "3",
    }]
    entry = await _setup(hass, **{
        CONF_TARIFFS: edited,
        CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01",
    })
    stored = entry.options[CONF_TARIFFS]
    assert len(stored) == 1
    assert stored[0]["water_availability"] == "9.99"


async def test_setup_does_not_resurrect_a_deleted_snapshot(hass: HomeAssistant):
    """The mark, not the newest stored date, is what makes a deletion stick."""
    entry = await _setup(hass, **{
        CONF_TARIFFS: [{
            "effective_from": "2024-12-12",
            "water_tier_bounds": [5, 10, 15, 25],
            "water_tier_prices": ["0.4751", "0.6206", "0.8048", "1.755", None],
            "water_availability": "4.5476", "sanitation_drainage": "0.4402",
            "sanitation_availability": "4.4635", "waste_variable": "0.0136",
            "waste_fixed": "2.331", "tax_water": "0.0379",
            "tax_sanitation": "0.0141", "tax_waste_mgmt": "2.426",
        }],
        CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01",
    })
    assert [t["effective_from"] for t in entry.options[CONF_TARIFFS]] == ["2024-12-12"]


async def test_setup_adds_a_snapshot_newer_than_the_mark(hass: HomeAssistant):
    """An older mark means a release has since shipped newer tariffs."""
    entry = await _setup(hass, **{
        CONF_TARIFFS: [{
            "effective_from": "2024-12-12",
            "water_tier_bounds": [5, 10, 15, 25],
            "water_tier_prices": ["0.4751", "0.6206", "0.8048", "1.755", None],
            "water_availability": "4.5476", "sanitation_drainage": "0.4402",
            "sanitation_availability": "4.4635", "waste_variable": "0.0136",
            "waste_fixed": "2.331", "tax_water": "0.0379",
            "tax_sanitation": "0.0141", "tax_waste_mgmt": "2.426",
        }],
        CONF_TARIFFS_SEEDED_THROUGH: "2025-01-01",
    })
    assert [t["effective_from"] for t in entry.options[CONF_TARIFFS]] == [
        "2024-12-12", "2026-01-01", "2026-02-01",
    ]


async def test_malformed_stored_tariffs_do_not_break_setup(hass: HomeAssistant):
    entry = await _setup(hass, **{
        CONF_TARIFFS: [{"effective_from": "not-a-date"}],
        CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01",
    })
    assert hass.states.get("sensor.agere_total_cost") is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/ha/test_tariff_seeding.py -q`
Expected: FAIL — `KeyError: 'tariffs'`, because nothing seeds them yet.

- [ ] **Step 3: Seed in `async_setup_entry`**

In `custom_components/agere_water/__init__.py`, add to the imports at the top:

```python
from .const import (
    CONF_READINGS, CONF_TARIFFS, CONF_TARIFFS_SEEDED_THROUGH, DOMAIN, PLATFORMS,
)
```

and inside `async_setup_entry`, before forwarding the platforms:

```python
    _async_seed_tariffs(hass, entry)
```

Then add the function (the imports stay local for the same reason the service
import does: this module must remain importable without Home Assistant):

```python
@callback
def _async_seed_tariffs(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Write the built-in tariffs, and later only ones newer than the mark.

    Nothing stored is ever overwritten, so an edited or deleted snapshot stays
    as the user left it. Options are written only when something is added,
    because writing them reloads the entry.
    """
    from .entry_options import (
        seeded_through_from_options, tariffs_from_options, tariffs_to_options,
    )
    from .tariffs import BUILTIN_SCHEDULE

    try:
        stored = tariffs_from_options(entry.options)
        mark = seeded_through_from_options(entry.options)
    except ValueError as err:
        _LOGGER.error(
            "Stored AGERE tariffs are invalid (%s); leaving them untouched and "
            "falling back to the built-in schedule. Fix them under Settings -> "
            "Devices & Services -> AGERE -> Configure -> Tariffs", err,
        )
        return

    merged, new_mark = stored.merge_newer(BUILTIN_SCHEDULE, mark)
    already = entry.options.get(CONF_TARIFFS) or []
    if len(merged) == len(already) and mark == new_mark:
        return

    hass.config_entries.async_update_entry(entry, options={
        **entry.options,
        CONF_TARIFFS: tariffs_to_options(merged),
        CONF_TARIFFS_SEEDED_THROUGH: new_mark.isoformat(),
    })
    _LOGGER.info(
        "Seeded the AGERE tariff schedule through %s (%d entries)",
        new_mark.isoformat(), len(merged),
    )
```

Add `callback` to the `homeassistant.core` import — currently `__init__.py`
imports nothing from there at runtime, so add a real import:

```python
from homeassistant.core import callback
```

This is safe: `homeassistant.core` is what every HA module imports, and the
engine tests never import this package's `__init__` — only its submodules.
Verify that assumption in step 4; if the engine tests break, drop the decorator
and keep the plain function.

- [ ] **Step 4: Run both suites**

Run: `.venv/bin/python -m pytest tests/ha/test_tariff_seeding.py -q`
Expected: PASS (6 tests)

Run: `python3 -m pytest -q`
Expected: engine tests PASS on the system Python, `tests/ha/` skipped. If this
fails with `ModuleNotFoundError: No module named 'homeassistant'`, the
`from homeassistant.core import callback` at module level broke the engine tests
— remove the decorator and the import.

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/__init__.py tests/ha/test_tariff_seeding.py
git commit -m "feat: seed the tariff schedule into the config entry

Later starts add only snapshots newer than the recorded mark, so a
release can ship next year's tariff without overwriting an edit or
resurrecting a deletion."
```

---

### Task 6: Rewire the sensors and services onto the new signature

**Files:**
- Modify: `custom_components/agere_water/sensor.py`
- Modify: `custom_components/agere_water/services.py`
- Modify: `tests/ha/test_sensor.py`
- Modify: `tests/ha/test_services.py`

**Interfaces:**
- Consumes: `calcular(start, end, consumption, config)`, `marginal_price(start, end, consumption, today, config)`, `Line`, `UnknownTariffValue` (Task 3); `tariffs_from_options` (Task 4).
- Produces:
  - `_AgereData.closed: list[tuple[Cycle, Breakdown | None]]` — `None` where the period could not be costed
  - `_AgereData.errors: dict[date, str]` — reason per uncostable period, keyed by cycle end
  - New `sensor.agere_total_cost` attributes: `tariff_effective_from`, `tariff_split`, `sub_periods`, and `lines` replacing `tiers`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ha/test_sensor.py`:

```python
async def test_total_cost_reports_the_tariff_and_the_split(hass: HomeAssistant):
    await _setup(hass, "543", **{
        CONF_READINGS: READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    a = hass.states.get("sensor.agere_total_cost").attributes
    assert a["tariff_effective_from"] == "2026-02-01"
    assert a["tariff_split"] is False
    assert a["sub_periods"] == []


async def test_total_cost_lines_replace_the_tiers_attribute(hass: HomeAssistant):
    await _setup(hass, "543", **{
        CONF_READINGS: READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    a = hass.states.get("sensor.agere_total_cost").attributes
    assert "tiers" not in a
    lines = a["lines"]
    assert {l["component"] for l in lines} >= {
        "water_tier_1", "water_availability", "sanitation_drainage",
        "waste_fixed", "tax_waste_mgmt",
    }
    fixed = next(l for l in lines if l["component"] == "waste_fixed")
    assert fixed["start"] is None
    assert fixed["vat"] is False


async def test_last_invoice_costs_old_periods_with_the_old_tariff(hass: HomeAssistant):
    """Two readings 30 days apart in 2025 must use the 2025 prices."""
    await _setup(hass, "543", **{CONF_READINGS: [
        {"date": "2025-03-01", "m3": "100", "source": "manual"},
        {"date": "2025-03-31", "m3": "120", "source": "manual"},
    ]})
    state = hass.states.get("sensor.agere_last_invoice")
    cycle = state.attributes["cycles"][0]
    assert cycle["days"] == 30
    assert cycle["m3"] == 20.0
    # 2025 prices, not 2026: the 2026 tariff would give 48.06
    assert cycle["total"] == 44.41


async def test_last_invoice_marks_a_period_it_cannot_cost(hass: HomeAssistant):
    """30 m3 in a 2025 period reaches the >25 tier, whose price is unknown. That
    period reports an error; the others keep their totals."""
    await _setup(hass, "543", **{CONF_READINGS: [
        {"date": "2025-03-01", "m3": "100", "source": "manual"},
        {"date": "2025-03-31", "m3": "130", "source": "manual"},
        {"date": "2025-04-30", "m3": "140", "source": "manual"},
    ]})
    cycles = hass.states.get("sensor.agere_last_invoice").attributes["cycles"]
    assert "error" in cycles[0]
    assert "tier 5" in cycles[0]["error"]
    assert "total" not in cycles[0]
    assert "total" in cycles[1]
```

Append to `tests/ha/test_services.py`:

```python
async def test_set_reading_response_reports_the_split(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-01-19", "m3": "500", "source": "manual"},
    ])
    response = await hass.services.async_call(
        DOMAIN, "set_reading", {"date": "2026-02-15", "m3": 515},
        blocking=True, return_response=True,
    )
    await hass.async_block_till_done()
    assert response["cycle"]["days"] == 27
    assert response["cycle"]["consumption_m3"] == 15.0
    assert response["cycle"]["tariff_split"] is True
    assert response["cycle"]["total"] == 35.80
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/ha -q`
Expected: FAIL — `calcular()` takes four positional arguments and the callers still pass three.

- [ ] **Step 3: Rewire `_AgereData` in `sensor.py`**

Replace the `_calc_config` body's `tariff=Tariff()` with the schedule, and change the imports:

```python
from .calculator import Line, calcular, marginal_price
from .entry_options import (
    next_reading_date_from_options, readings_from_options, readings_to_options,
    tariffs_from_options,
)
from .tariffs import UnknownTariffValue
```

`_calc_config` becomes:

```python
def _calc_config(options: dict) -> CalcConfig:
    try:
        vat_rate = Decimal(str(options.get(CONF_VAT_RATE, DEFAULT_VAT_RATE)))
    except (InvalidOperation, TypeError):
        vat_rate = DEFAULT_VAT_RATE
    try:
        schedule = tariffs_from_options(options)
    except ValueError:
        _LOGGER.error(
            "Stored AGERE tariffs are invalid; using the built-in schedule"
        )
        schedule = BUILTIN_SCHEDULE
    return CalcConfig(
        schedule=schedule,
        include_water=options.get(CONF_WATER, True),
        include_sanitation=options.get(CONF_SANITATION, True),
        include_waste=options.get(CONF_WASTE, True),
        include_taxes=options.get(CONF_TAXES, True),
        include_vat=options.get(CONF_INCLUDE_VAT, True),
        vat_rate=vat_rate,
    )
```

with `from .tariffs import BUILTIN_SCHEDULE, UnknownTariffValue` and the `Tariff`
import dropped.

In `recompute`, replace the calculation block:

```python
        today = dt_util.now().date()
        self.cycle = self.log.current_cycle(meter_total, self.next_reading_date)
        self.days_elapsed = days_elapsed(self.cycle, today)
        self.overdue = is_overdue(self.cycle, today)
        self.breakdown = calcular(
            self.cycle.start, self.cycle.end, self.cycle.consumption, self.config
        )
        self.marginal = marginal_price(
            self.cycle.start, self.cycle.end, self.cycle.consumption, today,
            self.config,
        )
        closed_cycles = self.log.closed_cycles()
        self.closed = []
        self.errors = {}
        for c in closed_cycles:
            try:
                self.closed.append(
                    (c, calcular(c.start, c.end, c.consumption, self.config))
                )
            except (UnknownTariffValue, ValueError) as err:
                # One uncostable period must not take the rest of the history
                # with it.
                self.closed.append((c, None))
                self.errors[c.end] = str(err)
        self.projected_m3 = project_consumption(
            self.cycle, self.days_elapsed, closed_cycles
        )
        self.forecast = calcular(
            self.cycle.start, self.cycle.end, self.projected_m3, self.config
        )
```

Initialise `self.errors = {}` next to `self.closed = []` in `__init__`.

If the live period itself cannot be costed, `recompute` raises and the sensors
keep their previous values. Guard it so the error is logged once per recompute
instead of propagating into the state machine: wrap the `self.breakdown = ...`
and `self.forecast = ...` assignments in the same `try`, set both to `None`, and
log at error level.

- [ ] **Step 4: Update the total-cost attributes**

In `AgereTotalCostSensor.extra_state_attributes`, replace the `tiers` entry with
the line list and the tariff information:

```python
            "tariff_effective_from": self._data.tariff_from.isoformat(),
            "tariff_split": len(self._data.sub_periods) > 1,
            "sub_periods": [
                {"start": s.start.isoformat(), "end": s.end.isoformat(),
                 "days": s.days, "m3": float(q)}
                for s, q in self._data.sub_periods
            ] if len(self._data.sub_periods) > 1 else [],
            "lines": [
                {
                    "component": l.component,
                    "start": l.start.isoformat() if l.start else None,
                    "end": l.end.isoformat() if l.end else None,
                    "m3": float(l.qty),
                    "eur_per_m3": float(l.rate),
                    "eur": float(l.value),
                    "vat": l.vat,
                }
                for l in bd.lines
            ],
```

`_AgereData` gains the two values these read, set in `recompute` right after the
breakdown:

```python
        from .calculator import allocate, sub_periods
        changes = self.config.schedule.change_dates_for(
            "water", self.cycle.start, self.cycle.end
        )
        subs = sub_periods(self.cycle.start, self.cycle.end, changes)
        self.sub_periods = list(zip(subs, allocate(self.cycle.consumption, subs)))
        self.tariff_from = next(
            p.effective_from for p in reversed(self.config.schedule.periods)
            if p.effective_from <= self.cycle.end
        )
```

Move those two imports to the top of the module with the others; the local import
above is only to show where they come from.

Initialise `self.sub_periods = []` and `self.tariff_from = None` in `__init__`,
and have `extra_state_attributes` return `None` while `tariff_from` is `None`.

- [ ] **Step 5: Add the error to the last-invoice cycles**

In `AgereLastInvoiceSensor`:

```python
    @property
    def native_value(self):
        costed = [bd for _, bd in self._data.closed if bd is not None]
        return float(costed[-1].total) if costed else None

    @property
    def extra_state_attributes(self):
        cycles = []
        for cycle, bd in self._data.closed:
            entry = {
                "start": cycle.start.isoformat(),
                "end": cycle.end.isoformat(),
                "days": cycle.days,
                "m3": float(cycle.consumption),
            }
            if bd is None:
                entry["error"] = self._data.errors.get(cycle.end, "not calculated")
            else:
                entry["total"] = float(bd.total)
            cycles.append(entry)
        return {"cycles": cycles, "readings": readings_to_options(self._data.log)}
```

- [ ] **Step 6: Update `services.py`**

`_cycle_response` takes the period's dates and reports the split:

```python
def _cycle_response(options: dict) -> dict:
    from .calculator import calcular
    from .sensor import _calc_config

    log = readings_from_options(options)
    closed = log.closed_cycles()
    if not closed:
        return {}
    cycle = closed[-1]
    config = _calc_config(options)
    try:
        bd = calcular(cycle.start, cycle.end, cycle.consumption, config)
    except ValueError as err:
        return {"cycle": {
            "start": cycle.start.isoformat(), "end": cycle.end.isoformat(),
            "days": cycle.days, "consumption_m3": float(cycle.consumption),
            "error": str(err),
        }}
    changes = config.schedule.change_dates_for("water", cycle.start, cycle.end)
    return {
        "cycle": {
            "start": cycle.start.isoformat(),
            "end": cycle.end.isoformat(),
            "days": cycle.days,
            "consumption_m3": float(cycle.consumption),
            "tariff_split": bool(changes),
            "total": float(bd.total),
            "water": float(bd.water),
            "sanitation": float(bd.sanitation),
            "waste": float(bd.waste),
            "taxes": float(bd.taxes),
            "vat": float(bd.vat),
        }
    }
```

`UnknownTariffValue` subclasses `ValueError`, so the `except` above covers it.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. The pre-existing test asserting `"tiers"` in the attributes must
be gone — it was replaced in step 1.

- [ ] **Step 8: Commit**

```bash
git add custom_components/agere_water/sensor.py custom_components/agere_water/services.py tests/ha/test_sensor.py tests/ha/test_services.py
git commit -m "feat: cost every period with the tariff in force for it

Closed periods are costed one at a time, so a period whose tariff has an
unknown value reports an error in the cycles attribute instead of taking
the whole history down with it.

sensor.agere_total_cost gains tariff_effective_from, tariff_split and
sub_periods, and its tiers attribute is replaced by lines, which carries
the sub-period and the VAT flag of every charge."
```

---

### Task 7: The tariff editor in the options flow

**Files:**
- Modify: `custom_components/agere_water/config_flow.py`
- Modify: `custom_components/agere_water/strings.json`
- Modify: `custom_components/agere_water/translations/en.json`
- Modify: `tests/ha/test_config_flow.py`

**Interfaces:**
- Consumes: `tariffs_from_options`, `tariffs_to_options` (Task 4); `Tariff`, `TariffPeriod` (Task 1); `CONF_TARIFFS` (Task 1).
- Produces: options steps `tariffs` and `tariff_edit`; module constant `NEW_TARIFF = "new"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ha/test_config_flow.py`:

```python
TARIFF_FORM = {
    "effective_from": "2027-01-01",
    "water_tier_bounds": "5,10,15,25",
    "water_tier_price_1": "0.52",
    "water_tier_price_2": "0.68",
    "water_tier_price_3": "0.88",
    "water_tier_price_4": "1.92",
    "water_tier_price_5": "2.75",
    "water_availability": "5.00",
    "sanitation_drainage": "0.49",
    "sanitation_availability": "5.00",
    "waste_variable": "0.015",
    "waste_fixed": "2.60",
    "tax_water": "0.0382",
    "tax_sanitation": "0.0150",
    "tax_waste_mgmt": "2.95",
    "delete": False,
}


async def _open_tariff(hass: HomeAssistant, entry: MockConfigEntry, value: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tariffs"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"tariff": value}
    )


async def test_options_menu_lists_four_sections(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert set(result["menu_options"]) == {
        "readings", "next_reading", "tariffs", "components",
    }


async def test_add_a_tariff_copies_the_newest_forward(hass: HomeAssistant):
    """The form for a new entry arrives pre-filled from the newest snapshot, so
    only what changed has to be typed."""
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    schema = result["data_schema"]({})
    assert schema["water_availability"] == "4.8623"
    assert schema["water_tier_price_1"] == "0.508"
    assert schema["water_tier_bounds"] == "5,10,15,25"


async def test_add_a_tariff(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], TARIFF_FORM
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    stored = entry.options[CONF_TARIFFS]
    added = next(t for t in stored if t["effective_from"] == "2027-01-01")
    assert added["water_availability"] == "5"
    assert added["water_tier_prices"] == ["0.52", "0.68", "0.88", "1.92", "2.75"]


async def test_edit_a_tariff(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "2026-02-01")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "effective_from": "2026-02-01",
                            "water_availability": "4.90"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    stored = {t["effective_from"]: t for t in entry.options[CONF_TARIFFS]}
    assert stored["2026-02-01"]["water_availability"] == "4.9"
    assert len(stored) == 4


async def test_delete_a_tariff(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "2026-01-01")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "effective_from": "2026-01-01",
                            "delete": True}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [t["effective_from"] for t in entry.options[CONF_TARIFFS]] == [
        "2024-12-12", "2025-01-01", "2026-02-01",
    ]


async def test_cannot_delete_the_last_tariff(hass: HomeAssistant):
    entry = await _entry(hass, **{CONF_TARIFFS: [{
        "effective_from": "2026-02-01",
        "water_tier_bounds": [5, 10, 15, 25],
        "water_tier_prices": ["0.508", "0.6636", "0.8605", "1.8765", "2.6852"],
        "water_availability": "4.8623", "sanitation_drainage": "0.4809",
        "sanitation_availability": "4.8766", "waste_variable": "0.0147",
        "waste_fixed": "2.5257", "tax_water": "0.0382",
        "tax_sanitation": "0.015", "tax_waste_mgmt": "2.8821",
    }], CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01"})
    result = await _open_tariff(hass, entry, "2026-02-01")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "effective_from": "2026-02-01",
                            "delete": True}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_tariff"
    assert "at least one" in result["description_placeholders"]["error"]


async def test_an_empty_top_tier_price_means_unknown(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "water_tier_price_5": ""}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    added = next(t for t in entry.options[CONF_TARIFFS]
                 if t["effective_from"] == "2027-01-01")
    assert added["water_tier_prices"][4] is None


async def test_invalid_tariff_value_shows_the_reason(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "waste_fixed": "abc"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_tariff"
    assert "waste_fixed" in result["description_placeholders"]["error"]
    assert entry.options[CONF_TARIFFS] != []


async def test_invalid_tier_bounds_show_the_reason(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "water_tier_bounds": "5,5,15,25"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_tariff"
    assert "increasing" in result["description_placeholders"]["error"]
```

Also add `CONF_TARIFFS` and `CONF_TARIFFS_SEEDED_THROUGH` to the imports at the
top of that file, and change `test_options_menu_lists_the_three_sections` to the
four-entry version above (delete the old one).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/ha/test_config_flow.py -q`
Expected: FAIL — the menu has three entries and there is no `tariffs` step.

- [ ] **Step 3: Add the steps to `config_flow.py`**

Add the imports and the module constant:

```python
from .entry_options import (
    format_decimal, readings_from_options, readings_to_options,
    tariffs_from_options, tariffs_to_options,
)
from .tariffs import Tariff, TariffPeriod

NEW_TARIFF = "new"

_TARIFF_VALUE_FIELDS = (
    "water_availability", "sanitation_drainage", "sanitation_availability",
    "waste_variable", "waste_fixed", "tax_water", "tax_sanitation",
    "tax_waste_mgmt",
)
```

Add `"tariffs"` to the menu:

```python
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["readings", "next_reading", "tariffs", "components"],
        )
```

Then the two steps:

```python
    # --- tariffs ---

    async def async_step_tariffs(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._selected_tariff = user_input["tariff"]
            return await self.async_step_tariff_edit()

        schedule = tariffs_from_options(self._entry.options)
        choices = _tariff_choices(schedule)
        choices.append(
            selector.SelectOptionDict(value=NEW_TARIFF, label="➕ New effective date")
        )
        return self.async_show_form(
            step_id="tariffs",
            data_schema=vol.Schema({
                vol.Required("tariff"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=choices, mode="dropdown")
                )
            }),
        )

    async def async_step_tariff_edit(self, user_input: dict[str, Any] | None = None):
        schedule = tariffs_from_options(self._entry.options)
        existing = next(
            (p for p in schedule.periods
             if p.effective_from.isoformat() == self._selected_tariff),
            None,
        )
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            try:
                if user_input.get("delete") and existing is not None:
                    new_schedule = schedule.remove(existing.effective_from)
                else:
                    base = (
                        schedule.remove(existing.effective_from)
                        if existing else schedule
                    )
                    new_schedule = base.set(_tariff_from_form(user_input))
            except (InvalidOperation, ValueError) as err:
                errors["base"] = "invalid_tariff"
                placeholders["error"] = str(err)
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        **self._entry.options,
                        CONF_TARIFFS: tariffs_to_options(new_schedule),
                    },
                )

        # Copy forward: the newest snapshot for a new entry, its own values when
        # editing. A new tariff always succeeds the latest one.
        source = existing.tariff if existing else schedule.at(schedule.latest)
        default_date = (
            existing.effective_from.isoformat() if existing else None
        )
        return self.async_show_form(
            step_id="tariff_edit",
            data_schema=_tariff_schema(source, default_date),
            errors=errors,
            description_placeholders=placeholders,
        )
```

And the module-level helpers:

```python
def _tariff_choices(schedule) -> list:
    """Dropdown rows, newest first, each annotated with what it changed."""
    periods = schedule.periods
    labels = {}
    for index, period in enumerate(periods):
        previous = periods[index - 1].tariff if index else None
        changed = _changed_fields(period.tariff, previous)
        label = period.effective_from.isoformat()
        if changed:
            label += " · " + ", ".join(changed)
        labels[period.effective_from] = label
    return [
        selector.SelectOptionDict(
            value=p.effective_from.isoformat(), label=labels[p.effective_from]
        )
        for p in reversed(periods)
    ]


def _changed_fields(tariff: Tariff, previous: Tariff | None) -> list[str]:
    if previous is None:
        return ["base"]
    changed = []
    if (tariff.water_tier_prices, tariff.water_tier_bounds) != (
        previous.water_tier_prices, previous.water_tier_bounds
    ):
        changed.append("water")
    for name in _TARIFF_VALUE_FIELDS:
        if getattr(tariff, name) != getattr(previous, name):
            changed.append(name.replace("_", " "))
    return changed


def _tariff_schema(source: Tariff, default_date: str | None) -> vol.Schema:
    """Text fields, not numbers: prices carry six decimals on the invoice and a
    NumberSelector routes them through float. Decimal from text is exact, and it
    is the convention vat_rate already uses.
    """
    def suggest(value):
        return {"suggested_value": None if value is None else format_decimal(value)}

    fields: dict = {
        vol.Required("effective_from",
                     description={"suggested_value": default_date}):
            selector.DateSelector(),
        vol.Required("water_tier_bounds", description={
            "suggested_value": ",".join(str(b) for b in source.water_tier_bounds)
        }): str,
    }
    for index, price in enumerate(source.water_tier_prices, start=1):
        key = f"water_tier_price_{index}"
        # The top tier may be unknown, so it is the only optional price.
        marker = vol.Optional if index == len(source.water_tier_prices) else vol.Required
        fields[marker(key, description=suggest(price))] = str
    for name in _TARIFF_VALUE_FIELDS:
        fields[vol.Required(name, description=suggest(getattr(source, name)))] = str
    fields[vol.Required("delete", default=False)] = bool
    return vol.Schema(fields)


def _tariff_from_form(user_input: dict[str, Any]) -> TariffPeriod:
    bounds_raw = str(user_input["water_tier_bounds"]).replace(" ", "")
    try:
        bounds = tuple(int(b) for b in bounds_raw.split(",") if b)
    except ValueError as err:
        raise ValueError(
            f"tier bounds must be whole numbers separated by commas, got "
            f"{user_input['water_tier_bounds']!r}"
        ) from err

    prices = []
    for index in range(1, len(bounds) + 2):
        raw = str(user_input.get(f"water_tier_price_{index}", "")).strip()
        prices.append(None if not raw else _parse(raw, f"water_tier_price_{index}"))

    values = {
        name: _parse(str(user_input[name]).strip(), name)
        for name in _TARIFF_VALUE_FIELDS
    }
    return TariffPeriod(
        effective_from=date.fromisoformat(user_input["effective_from"]),
        tariff=Tariff(
            water_tier_bounds=bounds, water_tier_prices=tuple(prices), **values
        ),
    )


def _parse(raw: str, field: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as err:
        raise ValueError(f"{field}: {raw!r} is not a number") from err
```

Add `self._selected_tariff: str | None = None` to `AgereWaterOptionsFlow.__init__`,
and `from datetime import date` plus `from decimal import Decimal, InvalidOperation`
are already imported at the top of the module.

`TariffSchedule` raises `ValueError` with "at least one entry" when the last
snapshot is removed, which is what `test_cannot_delete_the_last_tariff` asserts.

- [ ] **Step 4: Add the strings**

Add to `options.step` in **both** `strings.json` and `translations/en.json`:

```json
      "tariffs": {
        "title": "Tariffs",
        "description": "AGERE changes its tariff on dates that differ per component and never on the calendar year. Each entry holds a complete set of values from its effective date onwards. Pick one to edit or delete it.",
        "data": {
          "tariff": "Effective from"
        }
      },
      "tariff_edit": {
        "title": "Tariff",
        "description": "Values as printed in the invoice's 'Valor Unit.' column. A new entry starts pre-filled from the most recent one, so only what changed needs typing. Leave the top water tier empty if you do not know its price — it is better than guessing.",
        "data": {
          "effective_from": "Effective from",
          "water_tier_bounds": "Tier bounds in m³, comma separated (e.g. 5,10,15,25)",
          "water_tier_price_1": "Water 0-5 m³ (€/m³)",
          "water_tier_price_2": "Water 5-10 m³ (€/m³)",
          "water_tier_price_3": "Water 10-15 m³ (€/m³)",
          "water_tier_price_4": "Water 15-25 m³ (€/m³)",
          "water_tier_price_5": "Water above 25 m³ (€/m³), empty if unknown",
          "water_availability": "Water availability (€/period)",
          "sanitation_drainage": "Sanitation drainage (€/m³)",
          "sanitation_availability": "Sanitation availability (€/period)",
          "waste_variable": "Waste, variable (€/m³)",
          "waste_fixed": "Waste, fixed (€/period)",
          "tax_water": "Water resources tax (€/m³)",
          "tax_sanitation": "Sanitation resources tax (€/m³)",
          "tax_waste_mgmt": "Waste management tax (€/period)",
          "delete": "Delete this tariff"
        }
      },
```

and add to `options.init.menu_options`:

```json
          "tariffs": "Tariffs",
```

and to `options.error`:

```json
      "invalid_tariff": "{error}"
```

- [ ] **Step 5: Verify**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS
Run: `diff custom_components/agere_water/strings.json custom_components/agere_water/translations/en.json && echo identical`
Run: `python3 -c "import json;[json.load(open(p)) for p in ('custom_components/agere_water/strings.json','custom_components/agere_water/translations/en.json')];print('json ok')"`

- [ ] **Step 6: Commit**

```bash
git add custom_components/agere_water/config_flow.py custom_components/agere_water/strings.json custom_components/agere_water/translations/en.json tests/ha/test_config_flow.py
git commit -m "feat: edit the tariff schedule from the options flow

Text fields rather than number selectors: prices carry six decimals on
the invoice and a NumberSelector routes them through float. A new entry
is pre-filled from the most recent one, so only what changed is typed,
and the top water tier may be left empty to mean unknown."
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a Tariffs section to the README**

After the *Billing periods* section and before *Options*, add a `### Tariffs`
subsection covering:

- The integration ships the tariff calendar reconstructed from real Doméstico
  invoices, with four effective dates: `2024-12-12` (earliest with evidence),
  `2025-01-01` (resource taxes), `2026-01-01` (waste-management tax) and
  `2026-02-01` (water and sanitation). State plainly that the dates differ per
  component and never fall on the calendar year, which is why the schedule is a
  list of effective dates rather than a per-year table.
- **Configure → Tariffs** lists them, newest first, annotated with what each
  changed, and lets you add, edit or delete one. A new entry is pre-filled from
  the most recent, so a tariff update means typing only the values that moved.
- A period crossing an effective date is **split per component**, with this
  worked example:

```
Period 2026-01-20 → 2026-02-15 (27 days, 15 m³), tariff changed on 2026-02-01

  water    20/01–31/01   12 days   7 m³   tiers 2/4/6/10   old prices
           01/02–15/02   15 days   8 m³   tiers 3/5/8/13   new prices
  taxes    20/01–15/02   27 days  15 m³   unchanged, so a single line
  fixed    once, at the tariff in force on 2026-02-15
```

  and the note that the water tiers restart from zero in each sub-period, which
  is what the invoices show.
- Updates add only effective dates newer than the last one seeded, so a release
  can ship next year's tariff without overwriting an edit or bringing back a
  deletion.
- The top water tier (>25 m³) has **no known price before 2026-02-01** — it was
  never billed. A period that reaches it reports an error for that period rather
  than a plausible wrong number; fill the value in under Configure → Tariffs if
  you have an invoice that shows it.

- [ ] **Step 2: Update the Accuracy section**

Replace the two-bullet list with a paragraph stating that the engine is validated
against 19 real Doméstico invoices spanning both sides of the 2026-02-01 tariff
change, including two whose periods cross an effective date, and that 18 of the
19 reproduce to the cent. Keep the two published figures as the bullets they are
today. Add the known exception:

```markdown
The one exception is a 32-day period, where AGERE used a tier limit of 6 m³
(`ceil(5 × 32/30)`) while three independent 28-day invoices require rounding
(`10 × 28/30 = 9.333 → 9`, not 10). No single rule fits both, so rounding is
kept and that period computes 0.16 € high. A 31- or 32-day invoice would settle
it.
```

- [ ] **Step 3: Document the new attributes in the Sensors table**

Update the `sensor.agere_total_cost` row: it now carries `tariff_effective_from`,
`tariff_split`, `sub_periods` and `lines` (which replaces `tiers`). Update the
`sensor.agere_last_invoice` row: each entry in `cycles` has `total` **or**
`error`.

- [ ] **Step 4: Add the CHANGELOG entry**

Under a new `## [Unreleased]` heading:

```markdown
### Added
- Tariff schedule: the integration now knows AGERE's tariff values by effective
  date and applies the one in force for each billing period, so historical
  periods compute with historical prices. Four effective dates ship built in,
  reconstructed from real Doméstico invoices, and they are editable under
  **Configure → Tariffs**. Updates add only dates newer than the last one
  seeded, never overwriting an edit or restoring a deletion.
- `sensor.agere_total_cost` gained `tariff_effective_from`, `tariff_split` and
  `sub_periods`.

### Changed
- **Breaking:** the `tiers` attribute of `sensor.agere_total_cost` is replaced by
  `lines`, which carries every charge with its component, sub-period, quantity,
  rate, value and VAT liability — the same structure as an invoice line.
- Each entry in the `cycles` attribute of `sensor.agere_last_invoice` now has
  `total` **or** `error`. A period whose tariff has an unknown value reports the
  reason instead of taking the rest of the history down with it.

### Fixed
- A billing period crossing a tariff change is now split the way AGERE splits it:
  per component, at the dates that component's own rate changes, with the water
  tiers reprorated and restarted in each sub-period and the fixed charges billed
  once at the end-of-period tariff. Previously the whole period used one tariff,
  which overstated periods before 2026-02-01 by 2 to 3 € and misbilled the one
  period a year that crosses a change.
```

- [ ] **Step 5: Verify**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS
Run: `grep -rn "DEFAULT_TARIFF\|tariff=Tariff\|CalcConfig(tariff" custom_components/ tests/ README.md`
Expected: nothing.

- [ ] **Step 6: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document the tariff schedule and the per-component split"
```

---

## Verification before handing back

- [ ] `.venv/bin/python -m pytest -q` — full suite passes on Python 3.13
- [ ] `python3 -m pytest -q` — engine tests pass on Python 3.14, `tests/ha/` skipped
- [ ] The 19 real invoices in `docs/agere-invoices.local.md` were re-run against the finished engine: 18 exact, the 32-day period +0.16 €. Report the numbers; commit neither the script nor the data.
- [ ] `grep -rn "042\.DP\.\|C10EB030162" $(git ls-files)` returns nothing
- [ ] `strings.json` and `translations/en.json` are byte-identical and valid JSON
- [ ] `grep -rn "DEFAULT_TARIFF" custom_components/ tests/` returns nothing
- [ ] No version bump, no release, no push — all three are the user's call
