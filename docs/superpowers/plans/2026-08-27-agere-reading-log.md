# AGERE Reading Log (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed reset-day billing cycle with a meter-reading log, so billing periods match AGERE's actual read-to-read periods, editable from the frontend including for past months.

**Architecture:** A pure `readings.py` module owns the reading log and derives `Cycle` objects (start, end, days, consumption) from consecutive readings. Readings live in `entry.options`, so the options flow writes them natively and the existing update listener reloads and recomputes. `calculator.py` is untouched — it already takes `days` as a parameter. Three services provide scripted/bulk entry; an options-flow menu provides interactive editing.

**Tech Stack:** Python 3.13, Home Assistant custom integration, `voluptuous`, `homeassistant.helpers.selector`, pytest + `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-08-27-agere-reading-log-design.md`

## Global Constraints

- **Phase 1 only.** Historical external statistics (`agere_water:total_cost`, `async_add_external_statistics`) are explicitly out of scope for this plan and get their own plan later.
- **`calculator.py` must not change.** It is validated against three invoices; the whole point of this work is fixing its inputs.
- **`readings.py` must not import `homeassistant`.** Same rule already followed by `calculator.py` and the old `cycle.py`, so its tests run without the HA harness.
- **Money and volume are `Decimal` end to end.** Stored in options as strings (`"2631"`), the convention already used by `vat_rate`.
- **Reading date semantics:** the date of a reading is the **end of the billing period** (the `Período Faturação` end / `Leitura` date on the invoice), never the invoice issue date. Every user-facing string must say so.
- **Monotonicity:** `sensor.agere_total_cost` is `TOTAL_INCREASING`. Within a cycle, `days` must stay constant so the accumulated total never decreases. This is why an overdue cycle freezes its `days` instead of extending `end`.
- **`strings.json` and `translations/en.json` are byte-identical.** Every string change must be applied to both.
- **Local test environment limitation:** this machine has Python 3.14 and no `pytest_homeassistant_custom_component`, so only the pure-engine tests (Tasks 1-3) run locally. Tasks 4-7 write HA-harness tests that `pytest.importorskip` skips locally; they are verified by the CI job added in Task 8. Do not claim those tests pass locally — report them as skipped, and rely on CI.

---

### Task 1: Third invoice regression in the calculator

Proves the premise of the whole plan: the engine is already correct when handed the right number of days.

**Files:**
- Test: `tests/test_calculator.py`

**Interfaces:**
- Consumes: `calcular`, `tier_limits`, `water_lines` from `custom_components.agere_water.calculator` (unchanged).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the test**

Append to `tests/test_calculator.py`:

```python
def test_tier_limits_33_days_prorated():
    # 15 * 33/30 = 16.5 -> 17 (half up, matching AGERE's own proration)
    assert tier_limits(33, (5, 10, 15, 25)) == [6, 11, 17, 28]


def test_water_lines_20m3_33days():
    lines = water_lines(Decimal("20"), 33, DEFAULT_TARIFF)
    assert [l.qty for l in lines] == [Decimal(x) for x in (6, 5, 6, 3, 0)]
    assert [l.value for l in lines] == [
        Decimal("3.05"), Decimal("3.32"), Decimal("5.16"),
        Decimal("5.63"), Decimal("0.00"),
    ]


def test_full_bill_20m3_33days():
    """Invoice 042.DP.26080422002962699, period 2026-07-11 ~ 2026-08-12."""
    bd = calcular(Decimal("20"), 33, CalcConfig())
    assert bd.water == Decimal("22.02")
    assert bd.sanitation == Decimal("14.50")
    assert bd.waste == Decimal("2.82")
    assert bd.taxes == Decimal("3.94")
    assert bd.base_without_vat == Decimal("43.28")
    assert bd.vat == Decimal("2.25")
    assert bd.total == Decimal("45.53")
```

- [ ] **Step 2: Run the tests**

Run: `python3 -m pytest tests/test_calculator.py -q`
Expected: PASS. These are expected to pass immediately — `calculator.py` is already correct. If any fails, stop: the spec's premise is wrong and the plan needs revisiting.

- [ ] **Step 3: Commit**

```bash
git add tests/test_calculator.py
git commit -m "test: add 20 m3 / 33 days invoice regression"
```

---

### Task 2: `readings.py` — reading log and cycle derivation

**Files:**
- Create: `custom_components/agere_water/readings.py`
- Delete: `custom_components/agere_water/cycle.py`
- Create: `tests/test_readings.py`
- Delete: `tests/test_cycle.py`

**Interfaces:**
- Consumes: nothing (pure module, stdlib only).
- Produces:
  - `SOURCE_MANUAL: str = "manual"`, `SOURCE_AUTO: str = "auto"`, `DEFAULT_CYCLE_DAYS: int = 30`
  - `Reading(date: datetime.date, m3: Decimal, source: str = SOURCE_MANUAL)` — frozen dataclass
  - `Cycle(start: date, end: date, days: int, consumption: Decimal, estimated: bool)` — frozen dataclass
  - `ReadingLog(readings: Iterable[Reading] = ())` — validates in `__init__`, raises `ValueError`
  - `ReadingLog.readings -> list[Reading]` (ascending by date), `ReadingLog.last -> Reading | None`, `len(log)`
  - `ReadingLog.set(reading: Reading) -> ReadingLog` (upsert by date, returns a new log)
  - `ReadingLog.remove(when: date) -> ReadingLog` (raises `ValueError` if absent)
  - `ReadingLog.closed_cycles() -> list[Cycle]`
  - `ReadingLog.current_cycle(meter_total: Decimal, next_reading_date: date | None = None) -> Cycle | None`
  - `days_elapsed(cycle: Cycle, today: date) -> int`
  - `is_overdue(cycle: Cycle, today: date) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_readings.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from custom_components.agere_water.readings import (
    SOURCE_AUTO, SOURCE_MANUAL, Cycle, Reading, ReadingLog, days_elapsed,
    is_overdue,
)

# The three readings printed on the 2026-08-13 invoice.
INVOICE_READINGS = [
    Reading(date(2026, 6, 12), Decimal("2593")),
    Reading(date(2026, 7, 10), Decimal("2611")),
    Reading(date(2026, 8, 12), Decimal("2631")),
]


def test_empty_log():
    log = ReadingLog()
    assert len(log) == 0
    assert log.last is None
    assert log.closed_cycles() == []
    assert log.current_cycle(Decimal("2631")) is None


def test_readings_sorted_ascending():
    log = ReadingLog(reversed(INVOICE_READINGS))
    assert [r.date for r in log.readings] == [
        date(2026, 6, 12), date(2026, 7, 10), date(2026, 8, 12),
    ]
    assert log.last.m3 == Decimal("2631")


def test_closed_cycles_match_invoices():
    cycles = ReadingLog(INVOICE_READINGS).closed_cycles()
    assert cycles == [
        Cycle(date(2026, 6, 13), date(2026, 7, 10), 28, Decimal("18"), False),
        Cycle(date(2026, 7, 11), date(2026, 8, 12), 33, Decimal("20"), False),
    ]


def test_current_cycle_with_next_reading_date():
    log = ReadingLog(INVOICE_READINGS)
    cycle = log.current_cycle(Decimal("2638"), date(2026, 9, 3))
    assert cycle == Cycle(
        date(2026, 8, 13), date(2026, 9, 3), 22, Decimal("7"), False
    )


def test_current_cycle_estimates_from_previous_cycle():
    log = ReadingLog(INVOICE_READINGS)
    cycle = log.current_cycle(Decimal("2638"))
    # previous closed cycle was 33 days -> 08-13 .. 09-14
    assert cycle.start == date(2026, 8, 13)
    assert cycle.end == date(2026, 9, 14)
    assert cycle.days == 33
    assert cycle.estimated is True


def test_current_cycle_single_reading_defaults_to_30_days():
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("2631"), SOURCE_AUTO)])
    cycle = log.current_cycle(Decimal("2633"))
    assert cycle.days == 30
    assert cycle.end == date(2026, 9, 11)
    assert cycle.consumption == Decimal("2")
    assert cycle.estimated is True


def test_current_cycle_floors_consumption_when_meter_decreases():
    log = ReadingLog(INVOICE_READINGS)
    cycle = log.current_cycle(Decimal("2600"))  # meter replaced / reset
    assert cycle.consumption == Decimal("0")


def test_next_reading_date_before_cycle_start_rejected():
    log = ReadingLog(INVOICE_READINGS)
    with pytest.raises(ValueError, match="after"):
        log.current_cycle(Decimal("2638"), date(2026, 8, 12))


def test_set_inserts_in_order():
    log = ReadingLog([INVOICE_READINGS[0], INVOICE_READINGS[2]])
    log = log.set(INVOICE_READINGS[1])
    assert [r.date for r in log.readings] == [
        date(2026, 6, 12), date(2026, 7, 10), date(2026, 8, 12),
    ]


def test_set_replaces_same_date():
    log = ReadingLog(INVOICE_READINGS).set(
        Reading(date(2026, 8, 12), Decimal("2632"))
    )
    assert len(log) == 3
    assert log.last.m3 == Decimal("2632")


def test_set_is_immutable():
    original = ReadingLog(INVOICE_READINGS)
    original.set(Reading(date(2026, 9, 3), Decimal("2640")))
    assert len(original) == 3


def test_remove_existing():
    log = ReadingLog(INVOICE_READINGS).remove(date(2026, 7, 10))
    assert [r.date for r in log.readings] == [date(2026, 6, 12), date(2026, 8, 12)]
    assert log.closed_cycles()[0].days == 61


def test_remove_missing_raises():
    with pytest.raises(ValueError, match="no reading"):
        ReadingLog(INVOICE_READINGS).remove(date(2026, 1, 1))


def test_remove_last_reading_empties_log():
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("2631"))])
    assert len(log.remove(date(2026, 8, 12))) == 0


def test_duplicate_dates_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        ReadingLog([
            Reading(date(2026, 8, 12), Decimal("2631")),
            Reading(date(2026, 8, 12), Decimal("2632")),
        ])


def test_decreasing_m3_rejected():
    with pytest.raises(ValueError, match="lower than"):
        ReadingLog([
            Reading(date(2026, 7, 10), Decimal("2611")),
            Reading(date(2026, 8, 12), Decimal("2600")),
        ])


def test_negative_m3_rejected():
    with pytest.raises(ValueError, match="negative"):
        ReadingLog([Reading(date(2026, 8, 12), Decimal("-1"))])


def test_set_that_breaks_ordering_rejected():
    log = ReadingLog(INVOICE_READINGS)
    with pytest.raises(ValueError, match="lower than"):
        log.set(Reading(date(2026, 7, 10), Decimal("2700")))


def test_days_elapsed_inclusive_and_clamped():
    cycle = Cycle(date(2026, 7, 11), date(2026, 8, 12), 33, Decimal("20"), False)
    assert days_elapsed(cycle, date(2026, 7, 11)) == 1
    assert days_elapsed(cycle, date(2026, 8, 12)) == 33
    assert days_elapsed(cycle, date(2026, 8, 20)) == 33   # clamped, cycle frozen
    assert days_elapsed(cycle, date(2026, 7, 1)) == 1     # before start


def test_is_overdue():
    cycle = Cycle(date(2026, 7, 11), date(2026, 8, 12), 33, Decimal("20"), False)
    assert is_overdue(cycle, date(2026, 8, 12)) is False
    assert is_overdue(cycle, date(2026, 8, 13)) is True


def test_default_source_is_manual():
    assert Reading(date(2026, 8, 12), Decimal("2631")).source == SOURCE_MANUAL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_readings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.agere_water.readings'`

- [ ] **Step 3: Write the implementation**

Create `custom_components/agere_water/readings.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_readings.py -q`
Expected: PASS (21 tests)

- [ ] **Step 5: Delete the superseded module and its tests**

```bash
git rm custom_components/agere_water/cycle.py tests/test_cycle.py
```

`cycle.py` is still imported by `sensor.py`, which Task 4 rewires. Between this task and Task 4 the integration does not load — expected, and the pure tests still pass.

- [ ] **Step 6: Run the full local suite**

Run: `python3 -m pytest -q`
Expected: `test_calculator.py` and `test_readings.py` PASS; `test_config_flow.py` and `test_sensor.py` SKIPPED (no HA harness locally). No collection errors.

- [ ] **Step 7: Commit**

```bash
git add -A custom_components/agere_water/readings.py tests/test_readings.py
git commit -m "feat: derive billing cycles from a meter-reading log

Replaces cycle.py. AGERE bills between reading dates (28, 33, ~22 days
on real invoices), which a fixed day-of-month cannot track."
```

---

### Task 3: Options keys and the options <-> ReadingLog mapping

**Files:**
- Modify: `custom_components/agere_water/const.py`
- Create: `custom_components/agere_water/entry_options.py`
- Create: `tests/test_entry_options.py`

**Interfaces:**
- Consumes: `Reading`, `ReadingLog`, `SOURCE_MANUAL` from Task 2.
- Produces:
  - `CONF_READINGS = "readings"`, `CONF_NEXT_READING_DATE = "next_reading_date"` in `const.py`
  - `readings_from_options(options: Mapping[str, Any]) -> ReadingLog` (raises `ValueError` on malformed data)
  - `readings_to_options(log: ReadingLog) -> list[dict[str, str]]`
  - `next_reading_date_from_options(options: Mapping[str, Any]) -> date | None`

`CONF_RESET_DAY` stays in `const.py` for now — `config_flow.py` still imports it and Task 6 removes it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_entry_options.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from custom_components.agere_water.const import (
    CONF_NEXT_READING_DATE, CONF_READINGS,
)
from custom_components.agere_water.entry_options import (
    next_reading_date_from_options, readings_from_options, readings_to_options,
)
from custom_components.agere_water.readings import (
    SOURCE_AUTO, SOURCE_MANUAL, Reading, ReadingLog,
)

STORED = [
    {"date": "2026-07-10", "m3": "2611", "source": "manual"},
    {"date": "2026-08-12", "m3": "2631", "source": "auto"},
]


def test_readings_from_options_roundtrip():
    log = readings_from_options({CONF_READINGS: STORED})
    assert [r.date for r in log.readings] == [date(2026, 7, 10), date(2026, 8, 12)]
    assert log.last.m3 == Decimal("2631")
    assert log.last.source == SOURCE_AUTO
    assert readings_to_options(log) == STORED


def test_readings_from_options_missing_key():
    assert len(readings_from_options({})) == 0
    assert len(readings_from_options({CONF_READINGS: None})) == 0


def test_readings_from_options_defaults_source_to_manual():
    log = readings_from_options({CONF_READINGS: [{"date": "2026-08-12", "m3": "2631"}]})
    assert log.last.source == SOURCE_MANUAL


def test_readings_from_options_accepts_numeric_m3():
    """Values written by a service call arrive as float/int, not str."""
    log = readings_from_options({CONF_READINGS: [{"date": "2026-08-12", "m3": 2631}]})
    assert log.last.m3 == Decimal("2631")


def test_readings_from_options_rejects_malformed_date():
    with pytest.raises(ValueError):
        readings_from_options({CONF_READINGS: [{"date": "12-08-2026", "m3": "2631"}]})


def test_readings_from_options_propagates_log_validation():
    with pytest.raises(ValueError, match="lower than"):
        readings_from_options({CONF_READINGS: [
            {"date": "2026-07-10", "m3": "2611"},
            {"date": "2026-08-12", "m3": "2600"},
        ]})


def test_readings_to_options_stores_m3_as_string():
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("2631.500"))])
    assert readings_to_options(log) == [
        {"date": "2026-08-12", "m3": "2631.500", "source": "manual"}
    ]


def test_next_reading_date_from_options():
    assert next_reading_date_from_options(
        {CONF_NEXT_READING_DATE: "2026-09-03"}
    ) == date(2026, 9, 3)
    assert next_reading_date_from_options({}) is None
    assert next_reading_date_from_options({CONF_NEXT_READING_DATE: None}) is None
    assert next_reading_date_from_options({CONF_NEXT_READING_DATE: ""}) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_entry_options.py -q`
Expected: FAIL — `ImportError: cannot import name 'CONF_NEXT_READING_DATE'`

- [ ] **Step 3: Add the option keys to `const.py`**

In the `--- config entry keys ---` block, after `CONF_TAXES`:

```python
CONF_READINGS = "readings"
CONF_NEXT_READING_DATE = "next_reading_date"
```

- [ ] **Step 4: Write `entry_options.py`**

```python
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

from .const import CONF_NEXT_READING_DATE, CONF_READINGS
from .readings import SOURCE_MANUAL, Reading, ReadingLog


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


def readings_to_options(log: ReadingLog) -> list[dict[str, str]]:
    """Serialise a ReadingLog for storage in options."""
    return [
        {"date": r.date.isoformat(), "m3": str(r.m3), "source": r.source}
        for r in log.readings
    ]


def next_reading_date_from_options(options: Mapping[str, Any]) -> date | None:
    raw = options.get(CONF_NEXT_READING_DATE)
    return date.fromisoformat(raw) if raw else None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_entry_options.py -q`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add custom_components/agere_water/const.py custom_components/agere_water/entry_options.py tests/test_entry_options.py
git commit -m "feat: store the reading log in entry options"
```

---

### Task 4: Rewire the sensors onto the reading log

**Files:**
- Modify: `custom_components/agere_water/sensor.py` (replaces the `Store` + `CycleManager` machinery in `_AgereData`, adds one sensor)
- Modify: `tests/test_sensor.py`

**Interfaces:**
- Consumes: `readings_from_options`, `readings_to_options`, `next_reading_date_from_options` (Task 3); `Reading`, `ReadingLog`, `SOURCE_AUTO`, `Cycle`, `days_elapsed`, `is_overdue` (Task 2); `CONF_READINGS` (Task 3).
- Produces:
  - `_AgereData.log: ReadingLog`, `.cycle: Cycle | None`, `.breakdown: Breakdown | None`, `.closed: list[tuple[Cycle, Breakdown]]`, `.marginal: Decimal`, `.days_elapsed: int`, `.overdue: bool`
  - `sensor.agere_last_invoice` entity

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_sensor.py` with:

```python
from decimal import Decimal

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)

INVOICE_READINGS = [
    {"date": "2026-06-12", "m3": "2593", "source": "manual"},
    {"date": "2026-07-10", "m3": "2611", "source": "manual"},
    {"date": "2026-08-12", "m3": "2631", "source": "manual"},
]


async def _setup(hass: HomeAssistant, meter_state: str, **extra_options):
    hass.states.async_set("sensor.water_meter_total", meter_state,
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_WATER: True, CONF_SANITATION: True, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
            **extra_options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_created(hass: HomeAssistant):
    await _setup(hass, "2631", **{CONF_READINGS: INVOICE_READINGS})
    for entity_id in (
        "sensor.agere_total_cost", "sensor.agere_marginal_price",
        "sensor.agere_cycle_consumption", "sensor.agere_water_cost",
        "sensor.agere_last_invoice",
    ):
        assert hass.states.get(entity_id) is not None


async def test_last_invoice_reproduces_both_closed_cycles(hass: HomeAssistant):
    await _setup(hass, "2631", **{CONF_READINGS: INVOICE_READINGS})
    state = hass.states.get("sensor.agere_last_invoice")
    assert Decimal(state.state) == Decimal("45.53")
    cycles = state.attributes["cycles"]
    assert cycles[0] == {
        "start": "2026-06-13", "end": "2026-07-10",
        "days": 28, "m3": 18.0, "total": 44.21,
    }
    assert cycles[1] == {
        "start": "2026-07-11", "end": "2026-08-12",
        "days": 33, "m3": 20.0, "total": 45.53,
    }


async def test_current_cycle_uses_next_reading_date(hass: HomeAssistant):
    await _setup(hass, "2638", **{
        CONF_READINGS: INVOICE_READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    attrs = hass.states.get("sensor.agere_total_cost").attributes
    assert attrs["cycle_start"] == "2026-08-13"
    assert attrs["cycle_end"] == "2026-09-03"
    assert attrs["billing_days"] == 22
    assert attrs["billing_days_estimated"] is False
    assert attrs["next_reading_date"] == "2026-09-03"
    assert attrs["cycle_consumption_m3"] == 7.0


async def test_current_cycle_estimated_without_next_reading_date(hass: HomeAssistant):
    await _setup(hass, "2638", **{CONF_READINGS: INVOICE_READINGS})
    attrs = hass.states.get("sensor.agere_total_cost").attributes
    assert attrs["billing_days"] == 33          # learned from the previous cycle
    assert attrs["billing_days_estimated"] is True
    assert attrs["next_reading_date"] is None


async def test_total_cost_recomputes_on_source_change(hass: HomeAssistant):
    await _setup(hass, "2631", **{
        CONF_READINGS: INVOICE_READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    before = Decimal(hass.states.get("sensor.agere_total_cost").state)
    hass.states.async_set("sensor.water_meter_total", "2636",
                          {"unit_of_measurement": "m³"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.agere_cycle_consumption").state == "5.0"
    assert Decimal(hass.states.get("sensor.agere_total_cost").state) > before


async def test_empty_log_seeds_an_auto_reading(hass: HomeAssistant):
    entry = await _setup(hass, "2631")
    await hass.async_block_till_done()
    stored = entry.options[CONF_READINGS]
    assert len(stored) == 1
    assert stored[0]["m3"] == "2631"
    assert stored[0]["source"] == "auto"


async def test_seeding_does_not_repeat(hass: HomeAssistant):
    entry = await _setup(hass, "2631")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.water_meter_total", "2635",
                          {"unit_of_measurement": "m³"})
    await hass.async_block_till_done()
    assert len(entry.options[CONF_READINGS]) == 1


async def test_malformed_readings_do_not_break_setup(hass: HomeAssistant):
    await _setup(hass, "2631", **{
        CONF_READINGS: [{"date": "not-a-date", "m3": "2631"}],
    })
    assert hass.states.get("sensor.agere_total_cost") is not None
```

- [ ] **Step 2: Run the tests and record that they cannot be verified locally**

Run: `python3 -m pytest tests/test_sensor.py -q`
Expected locally: SKIPPED (no HA harness on this machine). These are verified by CI in Task 8 — do not report them as passing.

- [ ] **Step 3: Rewrite the `_AgereData` head of `sensor.py`**

Replace the imports of `Store` / `CycleManager` / `CONF_RESET_DAY` / `DEFAULT_RESET_DAY` and the whole `_AgereData` class with:

```python
import logging

from .entry_options import (
    next_reading_date_from_options, readings_from_options, readings_to_options,
)
from .readings import (
    SOURCE_AUTO, Cycle, Reading, ReadingLog, days_elapsed, is_overdue,
)

_LOGGER = logging.getLogger(__name__)


class _AgereData:
    """Owns the reading log and the latest computed breakdowns."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.source = entry.data[CONF_SOURCE]
        self.config = _calc_config(dict(entry.options))
        try:
            self.log = readings_from_options(entry.options)
        except ValueError as err:
            _LOGGER.error(
                "Stored AGERE readings are invalid (%s); starting from an empty "
                "log. Fix them in Settings -> Devices & Services -> AGERE -> "
                "Configure -> Readings", err,
            )
            self.log = ReadingLog()
        try:
            self.next_reading_date = next_reading_date_from_options(entry.options)
        except ValueError:
            _LOGGER.error("Stored next reading date is invalid; ignoring it")
            self.next_reading_date = None
        self.cycle: Cycle | None = None
        self.breakdown = None
        self.closed: list[tuple] = []
        self.marginal = Decimal("0")
        self.days_elapsed = 0
        self.overdue = False
        self._seeding = False
        self._listeners: list = []

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    @callback
    def recompute(self) -> None:
        state = self.hass.states.get(self.source)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return
        try:
            meter_total = Decimal(state.state)
        except InvalidOperation:
            return

        if len(self.log) == 0:
            self._seed(meter_total)
            return

        today = dt_util.now().date()
        self.cycle = self.log.current_cycle(meter_total, self.next_reading_date)
        self.days_elapsed = days_elapsed(self.cycle, today)
        self.overdue = is_overdue(self.cycle, today)
        self.breakdown = calcular(self.cycle.consumption, self.cycle.days, self.config)
        self.marginal = marginal_price(
            self.cycle.consumption, self.cycle.days, self.config
        )
        self.closed = [
            (c, calcular(c.consumption, c.days, self.config))
            for c in self.log.closed_cycles()
        ]
        for cb in self._listeners:
            cb()

    def _seed(self, meter_total: Decimal) -> None:
        """Write the first reading so later cycles have a starting point.

        Reproduces the previous behaviour: the first cycle is partial, because
        the baseline is captured now rather than at the real cycle start. The
        user replaces it with the reading from their latest invoice.
        """
        if self._seeding:
            return
        self._seeding = True
        seeded = ReadingLog(
            [Reading(dt_util.now().date(), meter_total, SOURCE_AUTO)]
        )
        options = {**self.entry.options, CONF_READINGS: readings_to_options(seeded)}
        self.hass.async_create_task(self._async_write_options(options))

    async def _async_write_options(self, options: dict) -> None:
        self.hass.config_entries.async_update_entry(self.entry, options=options)
```

The write triggers the update listener already registered in `__init__.py`, which reloads the entry; on reload the log is non-empty so `_seed` is not reached again. `_seeding` guards against a second call before the reload lands. `CONF_READINGS` must be added to the `.const` import list in `sensor.py`.

- [ ] **Step 4: Update the attributes of `AgereTotalCostSensor`**

In `extra_state_attributes`, replace the `days_elapsed` / `billing_days` pair with:

```python
            "days_elapsed": self._data.days_elapsed,
            "billing_days": self._data.cycle.days,
            "billing_days_estimated": self._data.cycle.estimated,
            "cycle_start": self._data.cycle.start.isoformat(),
            "cycle_end": self._data.cycle.end.isoformat(),
            "cycle_overdue": self._data.overdue,
            "next_reading_date": (
                self._data.next_reading_date.isoformat()
                if self._data.next_reading_date else None
            ),
```

Keep every other attribute as it is. `cycle_consumption_m3` now reads `float(self._data.cycle.consumption)`; make `AgereCycleConsumptionSensor.native_value` read the same source.

- [ ] **Step 5: Add the last-invoice sensor**

Append to `sensor.py`:

```python
class AgereLastInvoiceSensor(_AgereBase):
    """Total of the most recent CLOSED cycle, plus the full derived history."""

    _attr_name = "AGERE last invoice"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_last_invoice"
        self.entity_id = "sensor.agere_last_invoice"

    @property
    def native_value(self):
        if not self._data.closed:
            return None
        return float(self._data.closed[-1][1].total)

    @property
    def extra_state_attributes(self):
        return {
            "cycles": [
                {
                    "start": cycle.start.isoformat(),
                    "end": cycle.end.isoformat(),
                    "days": cycle.days,
                    "m3": float(cycle.consumption),
                    "total": float(bd.total),
                }
                for cycle, bd in self._data.closed
            ],
            "readings": readings_to_options(self._data.log),
        }
```

Import `EntityCategory` from `homeassistant.const`, and register the sensor in `async_setup_entry` next to the other three unconditional entities.

- [ ] **Step 6: Confirm the empty-log path stays safe**

`native_value` and `extra_state_attributes` of every sensor already return `None` when `self._data.breakdown` is `None`, which is the state while the log is empty. Verify that `AgereTotalCostSensor.extra_state_attributes` returns before touching `self._data.cycle`, then run the local suite:

Run: `python3 -m pytest -q`
Expected: `test_calculator.py`, `test_readings.py`, `test_entry_options.py` PASS; sensor and config-flow tests SKIPPED; no collection errors (a stale `cycle.py` import would surface here).

- [ ] **Step 7: Commit**

```bash
git add custom_components/agere_water/sensor.py tests/test_sensor.py
git commit -m "feat: compute cycles from the reading log, add last-invoice sensor"
```

---

### Task 5: Migrate existing entries from the v1 `Store`

**Files:**
- Modify: `custom_components/agere_water/__init__.py`
- Modify: `custom_components/agere_water/config_flow.py` (`VERSION = 2` only)
- Create: `tests/test_migration.py`

**Interfaces:**
- Consumes: `CONF_READINGS`, `DOMAIN`, `PLATFORMS` (`const.py`); `SOURCE_AUTO` (Task 2). The removed reset-day key is referenced as a local `_LEGACY_RESET_DAY` literal, not imported, so Task 6 can delete it from `const.py`.
- Produces: `async_migrate_entry(hass, entry) -> bool` in `__init__.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migration.py`:

```python
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_READINGS, CONF_SOURCE, CONF_VAT_RATE, DOMAIN,
)

LEGACY_RESET_DAY = "reset_day"


def _v1_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={LEGACY_RESET_DAY: 13, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_migration_converts_store_state_to_a_reading(hass: HomeAssistant):
    hass.states.async_set("sensor.water_meter_total", "2640",
                          {"unit_of_measurement": "m³"})
    entry = _v1_entry(hass)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
    await store.async_save({"cycle_start": "2026-08-13", "baseline": "2631"})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert LEGACY_RESET_DAY not in entry.options
    # cycle_start - 1 day, so the derived cycle still starts on 2026-08-13
    assert entry.options[CONF_READINGS] == [
        {"date": "2026-08-12", "m3": "2631", "source": "auto"}
    ]
    assert await store.async_load() is None


async def test_migration_without_store_starts_empty(hass: HomeAssistant):
    hass.states.async_set("sensor.water_meter_total", "2640",
                          {"unit_of_measurement": "m³"})
    entry = _v1_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert LEGACY_RESET_DAY not in entry.options
    # no prior state to preserve -> the sensor platform seeds a reading itself
    assert len(entry.options[CONF_READINGS]) == 1
    assert entry.options[CONF_READINGS][0]["m3"] == "2640"


async def test_migration_preserves_the_cycle_boundary(hass: HomeAssistant):
    hass.states.async_set("sensor.water_meter_total", "2651",
                          {"unit_of_measurement": "m³"})
    entry = _v1_entry(hass)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
    await store.async_save({"cycle_start": "2026-08-13", "baseline": "2631"})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    attrs = hass.states.get("sensor.agere_total_cost").attributes
    assert attrs["cycle_start"] == "2026-08-13"
    assert attrs["cycle_consumption_m3"] == 20.0   # 2651 - 2631, baseline kept
```

- [ ] **Step 2: Run the tests and record that they cannot be verified locally**

Run: `python3 -m pytest tests/test_migration.py -q`
Expected locally: SKIPPED. Verified by CI (Task 8).

- [ ] **Step 3: Write the migration**

Replace `custom_components/agere_water/__init__.py` with:

```python
"""AGERE Water Price integration."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .const import CONF_READINGS, DOMAIN, PLATFORMS
from .readings import SOURCE_AUTO

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Removed in v2; only referenced to strip it from migrated entries.
_LEGACY_RESET_DAY = "reset_day"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 (fixed reset day + Store baseline) -> v2 (reading log in options)."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        stored = await store.async_load()
        readings: list[dict[str, str]] = []
        if stored and stored.get("cycle_start") and stored.get("baseline") is not None:
            # The v1 cycle started on cycle_start, so the equivalent reading is
            # the day before it, carrying the same baseline value.
            when = date.fromisoformat(stored["cycle_start"]) - timedelta(days=1)
            readings = [{
                "date": when.isoformat(),
                "m3": str(stored["baseline"]),
                "source": SOURCE_AUTO,
            }]
        options = {
            k: v for k, v in entry.options.items() if k != _LEGACY_RESET_DAY
        }
        options[CONF_READINGS] = readings
        hass.config_entries.async_update_entry(entry, options=options, version=2)
        await store.async_remove()
        _LOGGER.info(
            "Migrated AGERE entry to the reading log (%d reading(s) carried over)",
            len(readings),
        )
    return True
```

- [ ] **Step 4: Bump the config-flow version**

In `config_flow.py`, change `VERSION = 1` to `VERSION = 2`.

- [ ] **Step 5: Verify locally as far as possible**

Run: `python3 -m pytest -q`
Expected: pure tests PASS, HA tests SKIPPED, no collection errors.

- [ ] **Step 6: Commit**

```bash
git add custom_components/agere_water/__init__.py custom_components/agere_water/config_flow.py tests/test_migration.py
git commit -m "feat: migrate v1 reset-day entries to the reading log

Converts the v1 Store state (cycle_start + baseline) into an equivalent
reading, so existing installs keep their cycle boundary and baseline."
```

---

### Task 6: Options flow — drop the reset day, add the readings menu

**Files:**
- Modify: `custom_components/agere_water/config_flow.py`
- Modify: `custom_components/agere_water/const.py` (remove `CONF_RESET_DAY`, `DEFAULT_RESET_DAY`)
- Modify: `custom_components/agere_water/strings.json`
- Modify: `custom_components/agere_water/translations/en.json`
- Modify: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `readings_from_options`, `readings_to_options` (Task 3); `Reading`, `SOURCE_MANUAL` (Task 2).
- Produces: options steps `init` (menu), `readings`, `reading_edit`, `next_reading`, `components`; module constant `NEW_READING = "new"`.

`__init__.py` already uses the `_LEGACY_RESET_DAY` literal from Task 5, so removing `CONF_RESET_DAY` from `const.py` breaks nothing there.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_config_flow.py` with:

```python
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)

INVOICE_READINGS = [
    {"date": "2026-07-10", "m3": "2611", "source": "manual"},
    {"date": "2026-08-12", "m3": "2631", "source": "manual"},
]


async def test_user_flow_creates_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE: "sensor.water_meter_total"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE] == "sensor.water_meter_total"
    assert result["options"][CONF_READINGS] == []
    assert "reset_day" not in result["options"]


async def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    hass.states.async_set("sensor.water_meter_total", "2638",
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_WATER: True, CONF_SANITATION: True, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
            CONF_READINGS: INVOICE_READINGS, **options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_menu_lists_the_three_sections(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert set(result["menu_options"]) == {"readings", "next_reading", "components"}


async def test_components_step_keeps_the_readings(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "components"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_WATER: True, CONF_SANITATION: False, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SANITATION] is False
    assert entry.options[CONF_READINGS] == INVOICE_READINGS   # untouched


async def _open_reading(hass: HomeAssistant, entry: MockConfigEntry, value: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "readings"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"reading": value}
    )


async def test_add_a_new_reading(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-09-03", "m3": 2638, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_READINGS][-1] == {
        "date": "2026-09-03", "m3": "2638", "source": "manual"
    }


async def test_edit_a_past_reading_date(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-07-10")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-07-12", "m3": 2611, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [r["date"] for r in entry.options[CONF_READINGS]] == [
        "2026-07-12", "2026-08-12"
    ]


async def test_delete_a_reading(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-07-10")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-07-10", "m3": 2611, "delete": True}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [r["date"] for r in entry.options[CONF_READINGS]] == ["2026-08-12"]


async def test_invalid_edit_shows_the_error_in_the_form(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-08-12")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-08-12", "m3": 2000, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_reading"
    assert entry.options[CONF_READINGS] == INVOICE_READINGS   # nothing written


async def test_set_and_clear_next_reading_date(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "next_reading"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-09-03", "clear": False}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] == "2026-09-03"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "next_reading"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-09-03", "clear": True}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] is None
```

- [ ] **Step 2: Run the tests and record that they cannot be verified locally**

Run: `python3 -m pytest tests/test_config_flow.py -q`
Expected locally: SKIPPED. Verified by CI (Task 8).

- [ ] **Step 3: Rewrite `config_flow.py`**

```python
"""Config and options flow for AGERE Water Price."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)
from .entry_options import readings_from_options, readings_to_options
from .readings import SOURCE_MANUAL, Reading

NEW_READING = "new"

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    }
)


def _components_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WATER, default=options.get(CONF_WATER, True)): bool,
            vol.Required(CONF_SANITATION, default=options.get(CONF_SANITATION, True)): bool,
            vol.Required(CONF_WASTE, default=options.get(CONF_WASTE, True)): bool,
            vol.Required(CONF_TAXES, default=options.get(CONF_TAXES, True)): bool,
            vol.Required(CONF_INCLUDE_VAT, default=options.get(CONF_INCLUDE_VAT, True)): bool,
            vol.Required(CONF_VAT_RATE, default=options.get(CONF_VAT_RATE, "0.06")): str,
        }
    )


class AgereWaterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(
                title="AGERE Water Price",
                data={CONF_SOURCE: user_input[CONF_SOURCE]},
                options={
                    CONF_WATER: True, CONF_SANITATION: True,
                    CONF_WASTE: True, CONF_TAXES: True,
                    CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
                    CONF_READINGS: [], CONF_NEXT_READING_DATE: None,
                },
            )
        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AgereWaterOptionsFlow(config_entry)


class AgereWaterOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._entry = config_entry
        self._selected: str | None = None

    # --- menu ---

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["readings", "next_reading", "components"],
        )

    # --- tariff components ---

    async def async_step_components(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self._entry.options, **user_input}
            )
        return self.async_show_form(
            step_id="components",
            data_schema=_components_schema(dict(self._entry.options)),
        )

    # --- readings ---

    async def async_step_readings(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._selected = user_input["reading"]
            return await self.async_step_reading_edit()

        log = readings_from_options(self._entry.options)
        cycles = {c.end: c for c in log.closed_cycles()}
        choices = []
        for reading in reversed(log.readings):
            cycle = cycles.get(reading.date)
            label = f"{reading.date.isoformat()} · {reading.m3} m³"
            if cycle is not None:
                label += f" · {cycle.days} d · {cycle.consumption} m³"
            choices.append(
                selector.SelectOptionDict(value=reading.date.isoformat(), label=label)
            )
        choices.append(
            selector.SelectOptionDict(value=NEW_READING, label="➕ New reading")
        )
        return self.async_show_form(
            step_id="readings",
            data_schema=vol.Schema({
                vol.Required("reading"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=choices, mode="dropdown")
                )
            }),
        )

    async def async_step_reading_edit(self, user_input: dict[str, Any] | None = None):
        log = readings_from_options(self._entry.options)
        existing = next(
            (r for r in log.readings if r.date.isoformat() == self._selected), None
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            new_log = None
            try:
                if user_input.get("delete") and existing is not None:
                    new_log = log.remove(existing.date)
                else:
                    # Remove first, then insert: never build an intermediate log
                    # that would fail validation on its own.
                    base = log.remove(existing.date) if existing else log
                    new_log = base.set(Reading(
                        date=date.fromisoformat(user_input["date"]),
                        m3=Decimal(str(user_input["m3"])),
                        source=SOURCE_MANUAL,
                    ))
            except (InvalidOperation, ValueError):
                errors["base"] = "invalid_reading"
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        **self._entry.options,
                        CONF_READINGS: readings_to_options(new_log),
                    },
                )

        default_date = existing.date.isoformat() if existing else None
        default_m3 = float(existing.m3) if existing else None
        schema = vol.Schema({
            vol.Required("date", description={"suggested_value": default_date}):
                selector.DateSelector(),
            vol.Required("m3", description={"suggested_value": default_m3}):
                selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, step="any", mode="box")
                ),
            vol.Required("delete", default=False): bool,
        })
        return self.async_show_form(
            step_id="reading_edit", data_schema=schema, errors=errors
        )

    # --- next reading date ---

    async def async_step_next_reading(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            value = None if user_input.get("clear") else user_input["date"]
            return self.async_create_entry(
                title="",
                data={**self._entry.options, CONF_NEXT_READING_DATE: value},
            )
        current = self._entry.options.get(CONF_NEXT_READING_DATE)
        return self.async_show_form(
            step_id="next_reading",
            data_schema=vol.Schema({
                vol.Required("date", description={"suggested_value": current}):
                    selector.DateSelector(),
                vol.Required("clear", default=False): bool,
            }),
        )
```

- [ ] **Step 4: Remove `CONF_RESET_DAY` from `const.py`**

Delete the `CONF_RESET_DAY` and `DEFAULT_RESET_DAY` lines. Nothing imports them any more (`sensor.py` lost them in Task 4, `__init__.py` uses `_LEGACY_RESET_DAY` from Task 5, `config_flow.py` was just rewritten).

- [ ] **Step 5: Update the strings**

Write this to **both** `custom_components/agere_water/strings.json` and `custom_components/agere_water/translations/en.json` (they must stay identical):

```json
{
  "config": {
    "step": {
      "user": {
        "title": "AGERE Water Price",
        "description": "Pick the water-meter sensor reporting cumulative consumption in m³. Billing periods come from meter readings, which you add afterwards under Configure.",
        "data": {
          "source_entity": "Water meter sensor (m³)"
        }
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "AGERE Water Price",
        "menu_options": {
          "readings": "Readings",
          "next_reading": "Next reading date",
          "components": "Charges and VAT"
        }
      },
      "readings": {
        "title": "Readings",
        "description": "Each reading is the END of a billing period — the 'Leitura' date on your invoice, not the invoice date. Pick one to edit or delete it.",
        "data": {
          "reading": "Reading"
        }
      },
      "reading_edit": {
        "title": "Reading",
        "description": "Date of the meter reading (end of the billing period) and the meter value in m³, both as printed on the AGERE invoice.",
        "data": {
          "date": "Reading date (end of billing period)",
          "m3": "Meter reading (m³)",
          "delete": "Delete this reading"
        }
      },
      "next_reading": {
        "title": "Next reading date",
        "description": "When the current period closes — the 'Período de Comunicação' date on your invoice. Without it, the period length is estimated from the previous one.",
        "data": {
          "date": "Next reading date",
          "clear": "Clear and estimate instead"
        }
      },
      "components": {
        "title": "Charges and VAT",
        "data": {
          "enable_water": "Include water charges",
          "enable_sanitation": "Include sanitation charges",
          "enable_waste": "Include waste charges",
          "enable_taxes": "Include state taxes",
          "include_vat": "Include VAT",
          "vat_rate": "VAT rate (e.g. 0.06)"
        }
      }
    },
    "error": {
      "invalid_reading": "Invalid reading: dates must stay in order and meter values must not decrease."
    }
  }
}
```

- [ ] **Step 6: Verify locally as far as possible**

Run: `python3 -m pytest -q`
Expected: pure tests PASS, HA tests SKIPPED, no collection errors.
Run: `python3 -c "import json;[json.load(open(p)) for p in ('custom_components/agere_water/strings.json','custom_components/agere_water/translations/en.json')];print('json ok')"`
Run: `diff custom_components/agere_water/strings.json custom_components/agere_water/translations/en.json && echo identical`
Expected: `json ok`, then `identical`.

- [ ] **Step 7: Commit**

```bash
git add custom_components/agere_water/config_flow.py custom_components/agere_water/const.py custom_components/agere_water/strings.json custom_components/agere_water/translations/en.json tests/test_config_flow.py
git commit -m "feat: manage readings from the options flow, drop the reset day"
```

---

### Task 7: Services — `set_reading`, `remove_reading`, `set_next_reading_date`

**Files:**
- Create: `custom_components/agere_water/services.py`
- Create: `custom_components/agere_water/services.yaml`
- Modify: `custom_components/agere_water/__init__.py` (register services once)
- Modify: `custom_components/agere_water/strings.json`, `custom_components/agere_water/translations/en.json`
- Create: `tests/test_services.py`

**Interfaces:**
- Consumes: `readings_from_options`, `readings_to_options` (Task 3); `Reading`, `SOURCE_MANUAL` (Task 2); `calcular` (`calculator.py`); `_calc_config` (`sensor.py`).
- Produces: `async_setup_services(hass) -> None`; services `agere_water.set_reading`, `agere_water.remove_reading`, `agere_water.set_next_reading_date`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_services.py`:

```python
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)


async def _entry(hass: HomeAssistant, readings=None) -> MockConfigEntry:
    hass.states.async_set("sensor.water_meter_total", "2631",
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_WATER: True, CONF_SANITATION: True, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
            CONF_READINGS: readings if readings is not None else [],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_set_reading_inserts_and_returns_the_cycle(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-07-10", "m3": "2611", "source": "manual"},
    ])
    response = await hass.services.async_call(
        DOMAIN, "set_reading",
        {"date": "2026-08-12", "m3": 2631},
        blocking=True, return_response=True,
    )
    await hass.async_block_till_done()
    assert response["cycle"] == {
        "start": "2026-07-11", "end": "2026-08-12", "days": 33,
        "consumption_m3": 20.0, "total": 45.53, "water": 22.02,
        "sanitation": 14.5, "waste": 2.82, "taxes": 3.94, "vat": 2.25,
    }
    assert entry.options[CONF_READINGS][-1]["m3"] == "2631"


async def test_set_reading_upserts_same_date(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-08-12", "m3": "2631", "source": "manual"},
    ])
    await hass.services.async_call(
        DOMAIN, "set_reading", {"date": "2026-08-12", "m3": 2632}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(entry.options[CONF_READINGS]) == 1
    assert entry.options[CONF_READINGS][0]["m3"] == "2632"


async def test_set_reading_rejects_decreasing_value(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-07-10", "m3": "2611", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="lower than"):
        await hass.services.async_call(
            DOMAIN, "set_reading", {"date": "2026-08-12", "m3": 2600}, blocking=True
        )


async def test_set_reading_without_m3_and_without_recorder_data(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-07-10", "m3": "2611", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="no meter value"):
        await hass.services.async_call(
            DOMAIN, "set_reading", {"date": "2026-08-12"}, blocking=True
        )


async def test_remove_reading(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-07-10", "m3": "2611", "source": "manual"},
        {"date": "2026-08-12", "m3": "2631", "source": "manual"},
    ])
    await hass.services.async_call(
        DOMAIN, "remove_reading", {"date": "2026-07-10"}, blocking=True
    )
    await hass.async_block_till_done()
    assert [r["date"] for r in entry.options[CONF_READINGS]] == ["2026-08-12"]


async def test_remove_missing_reading_raises(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-08-12", "m3": "2631", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="no reading"):
        await hass.services.async_call(
            DOMAIN, "remove_reading", {"date": "2026-01-01"}, blocking=True
        )


async def test_set_next_reading_date_and_clear(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-08-12", "m3": "2631", "source": "manual"},
    ])
    await hass.services.async_call(
        DOMAIN, "set_next_reading_date", {"date": "2026-09-03"}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] == "2026-09-03"

    await hass.services.async_call(DOMAIN, "set_next_reading_date", {}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] is None


async def test_set_next_reading_date_before_last_reading_rejected(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-08-12", "m3": "2631", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="must be after"):
        await hass.services.async_call(
            DOMAIN, "set_next_reading_date", {"date": "2026-08-01"}, blocking=True
        )


async def test_service_requires_config_entry_when_ambiguous(hass: HomeAssistant):
    await _entry(hass)
    second = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={CONF_READINGS: []},
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="config_entry"):
        await hass.services.async_call(
            DOMAIN, "set_reading", {"date": "2026-08-12", "m3": 2631}, blocking=True
        )
```

- [ ] **Step 2: Run the tests and record that they cannot be verified locally**

Run: `python3 -m pytest tests/test_services.py -q`
Expected locally: SKIPPED. Verified by CI (Task 8).

- [ ] **Step 3: Write `services.py`**

```python
"""Services for managing the AGERE meter-reading log."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .calculator import calcular
from .const import CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SOURCE, DOMAIN
from .entry_options import readings_from_options, readings_to_options
from .readings import SOURCE_MANUAL, Reading

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_READING = "set_reading"
SERVICE_REMOVE_READING = "remove_reading"
SERVICE_SET_NEXT_READING_DATE = "set_next_reading_date"

ATTR_CONFIG_ENTRY = "config_entry"
ATTR_DATE = "date"
ATTR_M3 = "m3"

_SET_READING_SCHEMA = vol.Schema({
    vol.Required(ATTR_DATE): cv.date,
    vol.Optional(ATTR_M3): vol.Coerce(float),
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
})

_REMOVE_READING_SCHEMA = vol.Schema({
    vol.Required(ATTR_DATE): cv.date,
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
})

_SET_NEXT_SCHEMA = vol.Schema({
    vol.Optional(ATTR_DATE): cv.date,
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
})


def _resolve_entry(hass: HomeAssistant, call: ServiceCall) -> ConfigEntry:
    entry_id = call.data.get(ATTR_CONFIG_ENTRY)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(f"unknown config_entry {entry_id}")
        return entry
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1:
        raise ServiceValidationError(
            "more than one AGERE entry is set up: pass config_entry to say which"
        )
    return entries[0]


async def _async_meter_value_on(
    hass: HomeAssistant, entry: ConfigEntry, when: date
) -> Decimal:
    """Last meter value recorded on `when`, from long-term statistics."""
    entity_id = entry.data[CONF_SOURCE]
    if "recorder" not in hass.config.components:
        raise ServiceValidationError(
            f"no meter value for {when.isoformat()}: the recorder is not "
            "running, so pass m3 explicitly (take it from the invoice)"
        )
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    start = dt_util.as_utc(datetime.combine(when, time.min))
    end = start + timedelta(days=1)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, "day", None,
        {"state"},
    )
    points = stats.get(entity_id) or []
    value = points[-1].get("state") if points else None
    if value is None:
        raise ServiceValidationError(
            f"no meter value for {when.isoformat()} in the history of "
            f"{entity_id}: pass m3 explicitly (take it from the invoice)"
        )
    return Decimal(str(value))


def _cycle_response(options: dict) -> dict:
    """Recompute the cycle ending on the newest reading, for the call response."""
    from .sensor import _calc_config

    log = readings_from_options(options)
    closed = log.closed_cycles()
    if not closed:
        return {}
    cycle = closed[-1]
    bd = calcular(cycle.consumption, cycle.days, _calc_config(options))
    return {
        "cycle": {
            "start": cycle.start.isoformat(),
            "end": cycle.end.isoformat(),
            "days": cycle.days,
            "consumption_m3": float(cycle.consumption),
            "total": float(bd.total),
            "water": float(bd.water),
            "sanitation": float(bd.sanitation),
            "waste": float(bd.waste),
            "taxes": float(bd.taxes),
            "vat": float(bd.vat),
        }
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the reading-log services once, at integration setup."""

    async def _async_set_reading(call: ServiceCall) -> dict:
        entry = _resolve_entry(hass, call)
        when: date = call.data[ATTR_DATE]
        if ATTR_M3 in call.data:
            try:
                m3 = Decimal(str(call.data[ATTR_M3]))
            except InvalidOperation as err:
                raise ServiceValidationError(f"invalid m3 value: {err}") from err
        else:
            m3 = await _async_meter_value_on(hass, entry, when)
        log = readings_from_options(entry.options)
        try:
            new_log = log.set(Reading(date=when, m3=m3, source=SOURCE_MANUAL))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        options = {**entry.options, CONF_READINGS: readings_to_options(new_log)}
        hass.config_entries.async_update_entry(entry, options=options)
        return _cycle_response(options)

    async def _async_remove_reading(call: ServiceCall) -> None:
        entry = _resolve_entry(hass, call)
        log = readings_from_options(entry.options)
        try:
            new_log = log.remove(call.data[ATTR_DATE])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_READINGS: readings_to_options(new_log)},
        )

    async def _async_set_next(call: ServiceCall) -> None:
        entry = _resolve_entry(hass, call)
        when: date | None = call.data.get(ATTR_DATE)
        log = readings_from_options(entry.options)
        if when is not None and log.last is not None:
            try:
                log.current_cycle(log.last.m3, when)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_NEXT_READING_DATE: when.isoformat() if when else None,
            },
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_READING, _async_set_reading,
        schema=_SET_READING_SCHEMA, supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_READING, _async_remove_reading,
        schema=_REMOVE_READING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_NEXT_READING_DATE, _async_set_next,
        schema=_SET_NEXT_SCHEMA,
    )
```

- [ ] **Step 4: Register the services from `__init__.py`**

Add `from .services import SERVICE_SET_READING, async_setup_services` at the top, and in `async_setup_entry`, before forwarding the platforms:

```python
    if not hass.services.has_service(DOMAIN, SERVICE_SET_READING):
        async_setup_services(hass)
```

- [ ] **Step 5: Write `services.yaml`**

```yaml
set_reading:
  fields:
    date:
      required: true
      example: "2026-08-12"
      selector:
        date:
    m3:
      required: false
      example: 2631
      selector:
        number:
          min: 0
          step: 0.001
          mode: box
          unit_of_measurement: "m³"
    config_entry:
      required: false
      selector:
        config_entry:
          integration: agere_water

remove_reading:
  fields:
    date:
      required: true
      example: "2026-08-12"
      selector:
        date:
    config_entry:
      required: false
      selector:
        config_entry:
          integration: agere_water

set_next_reading_date:
  fields:
    date:
      required: false
      example: "2026-09-03"
      selector:
        date:
    config_entry:
      required: false
      selector:
        config_entry:
          integration: agere_water
```

- [ ] **Step 6: Add the service strings**

Add this top-level `"services"` block to **both** `strings.json` and `translations/en.json`, after the `"options"` block:

```json
  "services": {
    "set_reading": {
      "name": "Set meter reading",
      "description": "Adds or replaces a meter reading. The date is the END of the billing period — the 'Leitura' date on the AGERE invoice, not the invoice date.",
      "fields": {
        "date": {
          "name": "Reading date",
          "description": "End of the billing period, as printed on the invoice."
        },
        "m3": {
          "name": "Meter reading (m³)",
          "description": "Meter value from the invoice. Leave empty to take it from the meter sensor's recorded history on that date."
        },
        "config_entry": {
          "name": "Integration entry",
          "description": "Which AGERE entry to update. Only needed with more than one."
        }
      }
    },
    "remove_reading": {
      "name": "Remove meter reading",
      "description": "Deletes the reading with this date from the log.",
      "fields": {
        "date": {
          "name": "Reading date",
          "description": "Date of the reading to delete."
        },
        "config_entry": {
          "name": "Integration entry",
          "description": "Which AGERE entry to update. Only needed with more than one."
        }
      }
    },
    "set_next_reading_date": {
      "name": "Set next reading date",
      "description": "Sets when the current billing period closes — the 'Período de Comunicação' date on the invoice. Leave the date empty to clear it and estimate from the previous period.",
      "fields": {
        "date": {
          "name": "Next reading date",
          "description": "When the current period closes. Empty clears it."
        },
        "config_entry": {
          "name": "Integration entry",
          "description": "Which AGERE entry to update. Only needed with more than one."
        }
      }
    }
  }
```

- [ ] **Step 7: Verify locally as far as possible**

Run: `python3 -m pytest -q`
Expected: pure tests PASS, HA tests SKIPPED, no collection errors.
Run: `python3 -c "import json;[json.load(open(p)) for p in ('custom_components/agere_water/strings.json','custom_components/agere_water/translations/en.json')];print('json ok')"`
Run: `diff custom_components/agere_water/strings.json custom_components/agere_water/translations/en.json && echo identical`
Run: `python3 -c "import yaml;yaml.safe_load(open('custom_components/agere_water/services.yaml'));print('yaml ok')"`
Expected: `json ok`, `identical`, `yaml ok`.

- [ ] **Step 8: Commit**

```bash
git add custom_components/agere_water/services.py custom_components/agere_water/services.yaml custom_components/agere_water/__init__.py custom_components/agere_water/strings.json custom_components/agere_water/translations/en.json tests/test_services.py
git commit -m "feat: add reading-log services with a cycle response"
```

---

### Task 8: Run the test suite in CI

The HA-harness tests written in Tasks 4-7 cannot run on this machine (Python 3.14, no `pytest_homeassistant_custom_component`), and the existing workflow only runs hassfest and HACS validation. Without this job those tests are never executed anywhere.

**Files:**
- Modify: `.github/workflows/validate.yml`
- Modify: `requirements_test.txt`

- [ ] **Step 1: Pin the test requirements**

`requirements_test.txt`:

```
pytest
pytest-asyncio
pytest-homeassistant-custom-component
```

- [ ] **Step 2: Add the job**

Append to `.github/workflows/validate.yml`:

```yaml
  tests:
    name: Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements_test.txt
      - run: pytest -q
```

- [ ] **Step 3: Verify the workflow parses**

Run: `python3 -c "import yaml;yaml.safe_load(open('.github/workflows/validate.yml'));print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/validate.yml requirements_test.txt
git commit -m "ci: run the test suite on Python 3.13"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Rewrite the README's Configuration section**

Remove the **Reset day** bullet. The initial setup form now asks only for the source entity. Add a *Billing periods* subsection after *Configuration* covering:

- The reading date is the **end of the billing period** (`Período Faturação` end / `Leitura` on the invoice), not the invoice date.
- Interactive editing: *Settings → Devices & Services → AGERE → Configure → Readings*, which lists stored readings with their derived period and lets you add, edit or delete one — including for past months.
- *Configure → Next reading date*, taken from *Período de Comunicação* on the invoice, so the in-progress period has an exact length instead of an estimate.
- Bulk entry from past invoices via *Developer tools → Actions*, with a worked example:

```yaml
action: agere_water.set_reading
data:
  date: "2026-08-12"
  m3: 2631
```

  and a note that the action returns the recomputed period, so the result can be compared against the invoice on the spot.

Rename the *Options* list heading content to match the new menu section *Charges and VAT*, and drop `reset_day` from it.

- [ ] **Step 2: Rewrite the Known limitation section**

Replace the current text. The first cycle is partial only **until you enter the reading from your latest invoice** — that is now the fix, not an unavoidable limitation. Keep two caveats:

- A period whose length was estimated from the previous one is flagged by the `billing_days_estimated` attribute; set the next reading date to make it exact.
- A period that runs past its expected end without a new reading freezes its length (flagged by `cycle_overdue`) so the running total stays monotonic for the Energy dashboard.

- [ ] **Step 3: Add the third invoice to Accuracy**

```markdown
- 20 m³ over 33 days → 45.53 € total (tier limits prorated to 6/11/17/28 m³
  for the 33-day cycle).
```

- [ ] **Step 4: Add the new sensor to the Sensors table**

```markdown
| `sensor.agere_last_invoice` | Total (EUR) of the most recent closed billing period. Attributes list every derived period (start, end, days, m³, total) and the reading log itself. |
```

Also add the new `sensor.agere_total_cost` attributes to its row description: cycle start/end, `billing_days`, `billing_days_estimated`, `cycle_overdue`, `next_reading_date`.

- [ ] **Step 5: Add the CHANGELOG entry**

Under a new `## [Unreleased]` heading, following the file's existing style. Do **not** bump the version — that happens in a separate release commit.

```markdown
### Changed
- **Breaking:** billing periods now come from meter readings instead of a fixed
  reset day, matching AGERE's own read-to-read periods. Existing entries migrate
  automatically: the previous cycle boundary and baseline are converted into an
  equivalent reading, so no consumption is lost.

### Added
- Reading log with `agere_water.set_reading`, `agere_water.remove_reading` and
  `agere_water.set_next_reading_date` services, and a Readings section under the
  integration's Configure menu for editing past periods.
- `sensor.agere_last_invoice`, exposing every reconstructed billing period.
- Test suite now runs in CI on Python 3.13.

### Fixed
- Period length no longer assumes a calendar month. On invoice
  042.DP.26080422002962699 (20 m³ over 33 days) the computed total was 46.99 €
  against 45.53 € billed; it is now exact.
```

- [ ] **Step 6: Verify**

Run: `python3 -m pytest -q`
Expected: pure tests PASS, HA tests SKIPPED.
Run: `grep -rn "reset day\|reset_day" README.md custom_components/`
Expected: only `_LEGACY_RESET_DAY` in `__init__.py`. Nothing in `README.md`.

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document the reading log and the third validated invoice"
```

---

## Verification before handing back

- [ ] `python3 -m pytest -q` — pure tests pass, HA tests skipped, no collection errors
- [ ] `grep -rn "CycleManager\|from .cycle\|CONF_RESET_DAY" custom_components/` returns nothing
- [ ] `strings.json` and `translations/en.json` are byte-identical and valid JSON
- [ ] `services.yaml` and `.github/workflows/validate.yml` parse as YAML
- [ ] The HA-harness tests (`test_sensor.py`, `test_config_flow.py`, `test_migration.py`, `test_services.py`) are reported as **unverified locally**, pending the CI run on push
- [ ] No version bump and no push — both are the user's call
