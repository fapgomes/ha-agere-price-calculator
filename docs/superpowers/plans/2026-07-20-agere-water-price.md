# AGERE Water Price Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Home Assistant custom integration that turns a water meter's m³ reading into AGERE (Doméstico tariff) cost sensors — tiered water charges, fixed charges, per-component sub-costs, a marginal €/m³ price, and an optional VAT.

**Architecture:** A pure `calculator.py` engine (no HA deps, Decimal money, per-line cent rounding) computes the bill breakdown from cycle consumption + elapsed days. A pure `cycle.py` tracks the billing cycle (reset on a configurable day-of-month, baseline meter reading, elapsed days), persisted via HA `Store`. `sensor.py` recomputes on every source-entity state change and exposes the entities. `config_flow.py` provides UI setup + options.

**Tech Stack:** Python 3.12+, Home Assistant custom component, `decimal.Decimal`, pytest + pytest-homeassistant-custom-component.

## Global Constraints

- Domain: `agere_water`. Entity object-ids in English (`total_cost`, `marginal_price`, `water_cost`, `sanitation_cost`, `waste_cost`, `taxes_cost`, `cycle_consumption`).
- All money math uses `decimal.Decimal`. **Every line item is quantized to 2 decimals with `ROUND_HALF_UP` before summing** (matches AGERE invoices to the cent).
- Tier limits are prorated by elapsed days: `round_half_up(base_limit × days / 30)`.
- VAT default 6%. VAT applies to water (all lines), sanitation (all lines), and the two water-resource taxes — **never** to waste lines nor to the waste-management tax.
- No external runtime `requirements` in `manifest.json` (pure calculation).
- Tariff defaults are the 2026 Doméstico values (see Task 1); all are overridable via options.

### Reference values (from the two 2026 invoices — the test oracle)

| Scenario | Consumo | Days | Água | Saneamento | Resíduos | Taxas | Base s/IVA | IVA (6%) | TOTAL |
|---|---|---|---|---|---|---|---|---|---|
| FAC 0049220236 | 28 m³ | 30 | 41,85 | 18,35 | 2,94 | 4,37 | 67,51 | 3,70 | 71,21 |
| FAC 0049259391 | 18 m³ | 28 | 21,86 | 13,54 | 2,79 | 3,84 | 42,03 | 2,18 | 44,21 |

Prorated tier limits: 30 days → [5,10,15,25]; 28 days → [5,9,14,23].

---

## Environment note (local execution)

This machine has only Python 3.11 and 3.14; Home Assistant needs 3.13. Decision: run **only the pure-engine tests (Tasks 2–6)** locally in a `pytest`-only venv. Tasks 7–8 are still implemented in full, but their HA-harness tests are not run here — they are validated on the user's real HA / CI later.

- The local venv installs **only `pytest`** (not `pytest-homeassistant-custom-component`).
- `conftest.py` loads the HA plugin **conditionally** so engine tests collect cleanly without it.
- `test_config_flow.py` and `test_sensor.py` start with `pytest.importorskip("pytest_homeassistant_custom_component")` so the full-suite `pytest` run skips them cleanly instead of erroring at import.
- For Tasks 7–8, "verify tests pass" locally means: `pytest -q` runs green with the HA modules **skipped**, and `python -c "import ast; ast.parse(open(f).read())"` confirms each new module parses. Real red→green happens on the user's HA.

## File Structure

```
custom_components/agere_water/
  __init__.py        # setup/unload config entry, reload on options change
  const.py           # DOMAIN, config keys, tariff/tariff-config dataclasses + defaults
  calculator.py      # pure billing engine (Decimal)
  cycle.py           # pure billing-cycle manager
  sensor.py          # sensor entities + recompute coordinator + Store persistence
  config_flow.py     # config flow + options flow
  manifest.json
  strings.json
  translations/en.json
tests/
  __init__.py
  conftest.py
  test_calculator.py
  test_cycle.py
  test_config_flow.py
  test_sensor.py
hacs.json
requirements_test.txt
README.md
```

---

## Task 1: Project scaffold, constants, and tariff config

**Files:**
- Create: `custom_components/agere_water/__init__.py` (empty placeholder for now: `"""AGERE Water Price integration."""`)
- Create: `custom_components/agere_water/manifest.json`
- Create: `custom_components/agere_water/const.py`
- Create: `hacs.json`
- Create: `requirements_test.txt`
- Create: `tests/__init__.py` (empty), `tests/conftest.py`
- Test: `tests/test_calculator.py` (first test only — defaults sanity)

**Interfaces:**
- Produces: `DOMAIN: str`; config-key constants (`CONF_SOURCE`, `CONF_RESET_DAY`, `CONF_INCLUDE_VAT`, `CONF_VAT_RATE`, `CONF_WATER`, `CONF_SANITATION`, `CONF_WASTE`, `CONF_TAXES`, plus one `CONF_TARIFF_*` per tariff value); dataclasses `Tariff` and `CalcConfig`; `DEFAULT_TARIFF: Tariff`, `DEFAULT_VAT_RATE: Decimal`, `DEFAULT_RESET_DAY: int`.

- [ ] **Step 1: Create the venv and test requirements**

`requirements_test.txt` (records the full set needed for the HA-harness tests in a proper 3.13 environment / CI):
```
pytest
pytest-homeassistant-custom-component
```

Local venv installs **only pytest** (see Environment note — this box has no Python 3.13, so the HA harness is not installed here):
```bash
python3.11 -m venv .venv && . .venv/bin/activate && pip install pytest
```
Expected: pytest installs cleanly. Do NOT `pip install -r requirements_test.txt` locally — it would pull `homeassistant`, which needs 3.13.

- [ ] **Step 2: Write `manifest.json`**

```json
{
  "domain": "agere_water",
  "name": "AGERE Water Price",
  "codeowners": ["@fapg"],
  "config_flow": true,
  "documentation": "https://github.com/fapg/ha-agere-price-calculator",
  "integration_type": "service",
  "iot_class": "calculated",
  "issue_tracker": "https://github.com/fapg/ha-agere-price-calculator/issues",
  "requirements": [],
  "version": "0.1.0"
}
```

- [ ] **Step 3: Write `hacs.json`**

```json
{
  "name": "AGERE Water Price",
  "render_readme": true,
  "homeassistant": "2024.1.0"
}
```

- [ ] **Step 4: Write `const.py`**

```python
"""Constants and tariff configuration for the AGERE Water Price integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

DOMAIN = "agere_water"
PLATFORMS = ["sensor"]

# --- config entry keys ---
CONF_SOURCE = "source_entity"
CONF_RESET_DAY = "reset_day"
CONF_INCLUDE_VAT = "include_vat"
CONF_VAT_RATE = "vat_rate"
CONF_WATER = "enable_water"
CONF_SANITATION = "enable_sanitation"
CONF_WASTE = "enable_waste"
CONF_TAXES = "enable_taxes"

# tariff override keys (stored as strings in options, parsed to Decimal)
CONF_WATER_TIER_PRICES = "water_tier_prices"      # list[str], length 5
CONF_WATER_TIER_BOUNDS = "water_tier_bounds"      # list[int], length 4
CONF_WATER_AVAILABILITY = "water_availability"
CONF_SANITATION_DRAINAGE = "sanitation_drainage"
CONF_SANITATION_AVAILABILITY = "sanitation_availability"
CONF_WASTE_VARIABLE = "waste_variable"
CONF_WASTE_FIXED = "waste_fixed"
CONF_TAX_WATER = "tax_water"
CONF_TAX_SANITATION = "tax_sanitation"
CONF_TAX_WASTE_MGMT = "tax_waste_mgmt"

DEFAULT_VAT_RATE = Decimal("0.06")
DEFAULT_RESET_DAY = 13


@dataclass(frozen=True)
class Tariff:
    """AGERE Doméstico tariff values (2026 defaults)."""

    water_tier_bounds: tuple[int, ...] = (5, 10, 15, 25)
    water_tier_prices: tuple[Decimal, ...] = (
        Decimal("0.5080"),
        Decimal("0.6636"),
        Decimal("0.8605"),
        Decimal("1.8765"),
        Decimal("2.6852"),
    )
    water_availability: Decimal = Decimal("4.8623")
    sanitation_drainage: Decimal = Decimal("0.4809")
    sanitation_availability: Decimal = Decimal("4.8766")
    waste_variable: Decimal = Decimal("0.0147")
    waste_fixed: Decimal = Decimal("2.5257")
    tax_water: Decimal = Decimal("0.0382")
    tax_sanitation: Decimal = Decimal("0.0150")
    tax_waste_mgmt: Decimal = Decimal("2.8821")


@dataclass(frozen=True)
class CalcConfig:
    """Everything the calculator needs beyond consumption and days."""

    tariff: Tariff = field(default_factory=Tariff)
    include_water: bool = True
    include_sanitation: bool = True
    include_waste: bool = True
    include_taxes: bool = True
    include_vat: bool = True
    vat_rate: Decimal = DEFAULT_VAT_RATE


DEFAULT_TARIFF = Tariff()
```

- [ ] **Step 5: Write `tests/conftest.py`**

```python
"""Shared test fixtures.

The Home Assistant test harness (pytest-homeassistant-custom-component) is only
present in a Python 3.13 environment / CI. When absent, engine tests
(test_calculator, test_cycle) still collect and run — the HA plugin and its
autouse fixture load only when the plugin is importable.
"""
import pytest

try:
    import pytest_homeassistant_custom_component  # noqa: F401

    _HAS_HA_HARNESS = True
except ImportError:
    _HAS_HA_HARNESS = False

if _HAS_HA_HARNESS:
    pytest_plugins = ["pytest_homeassistant_custom_component"]

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Enable loading custom integrations in all tests."""
        yield
```

- [ ] **Step 6: Write the first test in `tests/test_calculator.py`**

```python
from decimal import Decimal

from custom_components.agere_water.const import DEFAULT_TARIFF, CalcConfig


def test_default_tariff_values():
    assert DEFAULT_TARIFF.water_tier_bounds == (5, 10, 15, 25)
    assert DEFAULT_TARIFF.water_tier_prices[0] == Decimal("0.5080")
    assert DEFAULT_TARIFF.water_availability == Decimal("4.8623")


def test_calc_config_defaults():
    cfg = CalcConfig()
    assert cfg.include_vat is True
    assert cfg.vat_rate == Decimal("0.06")
    assert cfg.tariff.tax_waste_mgmt == Decimal("2.8821")
```

- [ ] **Step 7: Run tests**

Run: `. .venv/bin/activate && pytest tests/test_calculator.py -v`
Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add custom_components hacs.json requirements_test.txt tests
git commit -m "feat: scaffold agere_water integration with tariff constants"
```

---

## Task 2: Money rounding and prorated tier limits

**Files:**
- Create: `custom_components/agere_water/calculator.py`
- Test: `tests/test_calculator.py` (append)

**Interfaces:**
- Consumes: `Tariff`, `CalcConfig` from `const.py`.
- Produces: `money(value) -> Decimal` (quantize 0.01, ROUND_HALF_UP); `price4(value) -> Decimal` (quantize 0.0001, ROUND_HALF_UP); `tier_limits(days: int, bounds: tuple[int, ...]) -> list[int]`.

- [ ] **Step 1: Write failing tests**

```python
from custom_components.agere_water.calculator import money, price4, tier_limits


def test_money_rounds_half_up():
    assert money(Decimal("3.7014")) == Decimal("3.70")
    assert money(Decimal("13.4652")) == Decimal("13.47")
    assert money(Decimal("2.6544")) == Decimal("2.65")


def test_tier_limits_30_days_unchanged():
    assert tier_limits(30, (5, 10, 15, 25)) == [5, 10, 15, 25]


def test_tier_limits_28_days_prorated():
    assert tier_limits(28, (5, 10, 15, 25)) == [5, 9, 14, 23]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_calculator.py::test_tier_limits_28_days_prorated -v`
Expected: FAIL with `ImportError` / `cannot import name 'money'`.

- [ ] **Step 3: Implement**

```python
"""Pure AGERE Doméstico billing engine. No Home Assistant dependencies."""
from __future__ import annotations

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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_calculator.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/calculator.py tests/test_calculator.py
git commit -m "feat: add money rounding and prorated tier limits"
```

---

## Task 3: Water tier line breakdown

**Files:**
- Modify: `custom_components/agere_water/calculator.py`
- Test: `tests/test_calculator.py` (append)

**Interfaces:**
- Consumes: `money`, `tier_limits`, `Tariff`.
- Produces: `@dataclass TierLine(index: int, qty: Decimal, rate: Decimal, value: Decimal)`; `water_lines(consumo: Decimal, days: int, tariff: Tariff) -> list[TierLine]`.

- [ ] **Step 1: Write failing tests (both invoices)**

```python
from custom_components.agere_water.calculator import water_lines
from custom_components.agere_water.const import DEFAULT_TARIFF


def test_water_lines_28m3_30days():
    lines = water_lines(Decimal("28"), 30, DEFAULT_TARIFF)
    assert [l.qty for l in lines] == [Decimal(x) for x in (5, 5, 5, 10, 3)]
    assert [l.value for l in lines] == [
        Decimal("2.54"), Decimal("3.32"), Decimal("4.30"),
        Decimal("18.77"), Decimal("8.06"),
    ]
    assert sum(l.value for l in lines) == Decimal("36.99")


def test_water_lines_18m3_28days():
    lines = water_lines(Decimal("18"), 28, DEFAULT_TARIFF)
    assert [l.qty for l in lines] == [Decimal(x) for x in (5, 4, 5, 4, 0)]
    assert [l.value for l in lines] == [
        Decimal("2.54"), Decimal("2.65"), Decimal("4.30"),
        Decimal("7.51"), Decimal("0.00"),
    ]
    assert sum(l.value for l in lines) == Decimal("17.00")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_calculator.py::test_water_lines_18m3_28days -v`
Expected: FAIL with `cannot import name 'water_lines'`.

- [ ] **Step 3: Implement (append to `calculator.py`)**

```python
from dataclasses import dataclass


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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_calculator.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/calculator.py tests/test_calculator.py
git commit -m "feat: add water tier line breakdown"
```

---

## Task 4: Full bill breakdown with VAT and component toggles

**Files:**
- Modify: `custom_components/agere_water/calculator.py`
- Test: `tests/test_calculator.py` (append)

**Interfaces:**
- Consumes: `water_lines`, `money`, `TierLine`, `CalcConfig`.
- Produces: `@dataclass Breakdown(water, sanitation, waste, taxes, base_without_vat, vat, total: Decimal, lines: list[TierLine])`; `calcular(consumo: Decimal, days: int, config: CalcConfig) -> Breakdown`.

- [ ] **Step 1: Write failing tests (invoice totals + toggles)**

```python
from custom_components.agere_water.calculator import calcular
from custom_components.agere_water.const import CalcConfig


def test_full_bill_28m3_30days():
    bd = calcular(Decimal("28"), 30, CalcConfig())
    assert bd.water == Decimal("41.85")
    assert bd.sanitation == Decimal("18.35")
    assert bd.waste == Decimal("2.94")
    assert bd.taxes == Decimal("4.37")
    assert bd.base_without_vat == Decimal("67.51")
    assert bd.vat == Decimal("3.70")
    assert bd.total == Decimal("71.21")


def test_full_bill_18m3_28days():
    bd = calcular(Decimal("18"), 28, CalcConfig())
    assert bd.water == Decimal("21.86")
    assert bd.sanitation == Decimal("13.54")
    assert bd.waste == Decimal("2.79")
    assert bd.taxes == Decimal("3.84")
    assert bd.base_without_vat == Decimal("42.03")
    assert bd.vat == Decimal("2.18")
    assert bd.total == Decimal("44.21")


def test_vat_excluded():
    bd = calcular(Decimal("28"), 30, CalcConfig(include_vat=False))
    assert bd.vat == Decimal("0.00")
    assert bd.total == Decimal("67.51")


def test_water_only_component():
    bd = calcular(
        Decimal("28"), 30,
        CalcConfig(include_sanitation=False, include_waste=False, include_taxes=False),
    )
    assert bd.sanitation == Decimal("0")
    assert bd.water == Decimal("41.85")
    # VAT only on water: 41.85 * 0.06 = 2.511 -> 2.51
    assert bd.vat == Decimal("2.51")
    assert bd.total == Decimal("44.36")


def test_waste_never_taxed():
    # Waste-only, VAT on: waste is not subject to VAT, so vat must be 0.
    bd = calcular(
        Decimal("28"), 30,
        CalcConfig(include_water=False, include_sanitation=False, include_taxes=False),
    )
    assert bd.waste == Decimal("2.94")
    assert bd.vat == Decimal("0.00")
    assert bd.total == Decimal("2.94")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_calculator.py::test_full_bill_28m3_30days -v`
Expected: FAIL with `cannot import name 'calcular'`.

- [ ] **Step 3: Implement (append to `calculator.py`)**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_calculator.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/calculator.py tests/test_calculator.py
git commit -m "feat: add full bill breakdown with VAT and component toggles"
```

---

## Task 5: Marginal price

**Files:**
- Modify: `custom_components/agere_water/calculator.py`
- Test: `tests/test_calculator.py` (append)

**Interfaces:**
- Consumes: `tier_limits`, `price4`, `CalcConfig`.
- Produces: `marginal_price(consumo: Decimal, days: int, config: CalcConfig) -> Decimal` — the cost of the next m³ right now: current water tier rate + enabled per-m³ variable rates, with VAT applied to the subject portion (water tier + drainage + resource taxes) and waste-variable added VAT-free.

- [ ] **Step 1: Write failing tests**

```python
from custom_components.agere_water.calculator import marginal_price


def test_marginal_price_first_tier_no_vat_water_only():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    # 0 m³ consumed -> next m³ in tier 0 -> 0.5080
    assert marginal_price(Decimal("0"), 30, cfg) == Decimal("0.5080")


def test_marginal_price_selects_tier_by_consumption():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    # 12 m³ (30-day limits 5/10/15/25) -> next m³ in tier [10-15] -> 0.8605
    assert marginal_price(Decimal("12"), 30, cfg) == Decimal("0.8605")


def test_marginal_price_top_tier():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    assert marginal_price(Decimal("40"), 30, cfg) == Decimal("2.6852")


def test_marginal_price_full_with_vat():
    cfg = CalcConfig()  # all components + 6% VAT
    # tier 0 water 0.5080 + drainage 0.4809 + tax_water 0.0382 + tax_sanit 0.0150 = 1.0421
    # subject *1.06 = 1.104626 ; + waste_variable 0.0147 (no VAT) = 1.119326 -> 1.1193
    assert marginal_price(Decimal("0"), 30, cfg) == Decimal("1.1193")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_calculator.py::test_marginal_price_full_with_vat -v`
Expected: FAIL with `cannot import name 'marginal_price'`.

- [ ] **Step 3: Implement (append to `calculator.py`)**

```python
def marginal_price(consumo: Decimal, days: int, config: CalcConfig) -> Decimal:
    """Cost of the next cubic metre at the current cycle position (EUR/m³)."""
    t = config.tariff
    limits = tier_limits(days, t.water_tier_bounds)

    # current tier index: first tier whose upper limit the consumption has not reached
    idx = len(t.water_tier_prices) - 1
    for i, limit in enumerate(limits):
        if consumo < Decimal(limit):
            idx = i
            break

    subject = Decimal(0)          # portion subject to VAT
    vat_free = Decimal(0)         # portion never subject to VAT (waste variable)
    if config.include_water:
        subject += t.water_tier_prices[idx]
    if config.include_sanitation:
        subject += t.sanitation_drainage
    if config.include_taxes:
        subject += t.tax_water + t.tax_sanitation
    if config.include_waste:
        vat_free += t.waste_variable

    if config.include_vat:
        subject = subject * (Decimal(1) + config.vat_rate)
    return price4(subject + vat_free)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_calculator.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/calculator.py tests/test_calculator.py
git commit -m "feat: add marginal price calculation"
```

---

## Task 6: Billing-cycle manager

**Files:**
- Create: `custom_components/agere_water/cycle.py`
- Test: `tests/test_cycle.py`

**Interfaces:**
- Produces:
  - `@dataclass CycleState(cycle_start: date, baseline: Decimal)`
  - `last_reset_on_or_before(day: date, reset_day: int) -> date`
  - `class CycleManager(reset_day: int, state: CycleState | None = None)` with:
    - `update(now: date, meter_total: Decimal) -> CycleState` — initializes on first call (baseline = current reading, cycle_start = last reset boundary ≤ now); rolls the cycle (new baseline + start) when `now` has crossed into a new reset period; returns current state.
    - `consumption(meter_total: Decimal) -> Decimal` — `meter_total − baseline`, floored at 0.
    - `days_elapsed(now: date) -> int` — `(now − cycle_start).days + 1`, floored at 1.
    - `.state` property exposing the current `CycleState | None`.

- [ ] **Step 1: Write failing tests**

```python
from datetime import date
from decimal import Decimal

from custom_components.agere_water.cycle import (
    CycleManager, CycleState, last_reset_on_or_before,
)


def test_last_reset_same_month():
    assert last_reset_on_or_before(date(2026, 7, 20), 13) == date(2026, 7, 13)


def test_last_reset_previous_month():
    assert last_reset_on_or_before(date(2026, 7, 5), 13) == date(2026, 6, 13)


def test_last_reset_january_wraps_to_december():
    assert last_reset_on_or_before(date(2026, 1, 5), 13) == date(2025, 12, 13)


def test_first_update_initializes_baseline():
    mgr = CycleManager(reset_day=13)
    st = mgr.update(date(2026, 7, 20), Decimal("2620"))
    assert st.cycle_start == date(2026, 7, 13)
    assert st.baseline == Decimal("2620")
    assert mgr.consumption(Decimal("2625")) == Decimal("5")


def test_days_elapsed_inclusive():
    mgr = CycleManager(reset_day=13)
    mgr.update(date(2026, 7, 13), Decimal("2620"))
    assert mgr.days_elapsed(date(2026, 7, 13)) == 1
    assert mgr.days_elapsed(date(2026, 8, 11)) == 30


def test_cycle_rolls_and_resets_baseline():
    mgr = CycleManager(reset_day=13)
    mgr.update(date(2026, 7, 20), Decimal("2620"))
    # cross into next cycle
    st = mgr.update(date(2026, 8, 15), Decimal("2648"))
    assert st.cycle_start == date(2026, 8, 13)
    assert st.baseline == Decimal("2648")
    assert mgr.consumption(Decimal("2650")) == Decimal("2")


def test_no_roll_within_same_cycle():
    mgr = CycleManager(reset_day=13)
    mgr.update(date(2026, 7, 20), Decimal("2620"))
    st = mgr.update(date(2026, 8, 1), Decimal("2630"))
    assert st.cycle_start == date(2026, 7, 13)
    assert st.baseline == Decimal("2620")
    assert mgr.consumption(Decimal("2630")) == Decimal("10")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cycle.py -v`
Expected: FAIL with `ModuleNotFoundError` / `cannot import name`.

- [ ] **Step 3: Implement `cycle.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_cycle.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add custom_components/agere_water/cycle.py tests/test_cycle.py
git commit -m "feat: add billing-cycle manager"
```

---

## Task 7: Config flow, options flow, and entry setup

**Files:**
- Modify: `custom_components/agere_water/__init__.py`
- Create: `custom_components/agere_water/config_flow.py`
- Create: `custom_components/agere_water/strings.json`
- Create: `custom_components/agere_water/translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `DOMAIN`, `PLATFORMS`, all `CONF_*`, `DEFAULT_RESET_DAY` from `const.py`.
- Produces: `async_setup_entry(hass, entry) -> bool`, `async_unload_entry(hass, entry) -> bool`, `async_reload_entry(hass, entry) -> None` in `__init__.py`; `AgereWaterConfigFlow` + `AgereWaterOptionsFlow` in `config_flow.py`. The config-entry `data` holds `CONF_SOURCE`; `options` holds all the tunables (reset day, component toggles, VAT, tariff overrides).

- [ ] **Step 1: Write failing test**

```python
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.agere_water.const import (
    CONF_RESET_DAY, CONF_SOURCE, DOMAIN,
)


async def test_user_flow_creates_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SOURCE: "sensor.water_meter_total", CONF_RESET_DAY: 13},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE] == "sensor.water_meter_total"
    assert result["options"][CONF_RESET_DAY] == 13
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_config_flow.py -v`
Expected (local, no HA harness): the module is **skipped** via `importorskip`. That is the expected local state — red→green for this test runs on the user's HA / CI. Proceed to implement.

- [ ] **Step 3: Implement `config_flow.py`**

```python
"""Config and options flow for AGERE Water Price."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_INCLUDE_VAT, CONF_RESET_DAY, CONF_SANITATION, CONF_SOURCE,
    CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DEFAULT_RESET_DAY,
    DOMAIN,
)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Required(CONF_RESET_DAY, default=DEFAULT_RESET_DAY): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=28, mode="box")
        ),
    }
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_RESET_DAY, default=options.get(CONF_RESET_DAY, DEFAULT_RESET_DAY)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=28, mode="box")),
            vol.Required(CONF_WATER, default=options.get(CONF_WATER, True)): bool,
            vol.Required(CONF_SANITATION, default=options.get(CONF_SANITATION, True)): bool,
            vol.Required(CONF_WASTE, default=options.get(CONF_WASTE, True)): bool,
            vol.Required(CONF_TAXES, default=options.get(CONF_TAXES, True)): bool,
            vol.Required(CONF_INCLUDE_VAT, default=options.get(CONF_INCLUDE_VAT, True)): bool,
            vol.Required(CONF_VAT_RATE, default=options.get(CONF_VAT_RATE, "0.06")): str,
        }
    )


class AgereWaterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            reset_day = int(user_input[CONF_RESET_DAY])
            return self.async_create_entry(
                title="AGERE Water Price",
                data={CONF_SOURCE: user_input[CONF_SOURCE]},
                options={
                    CONF_RESET_DAY: reset_day,
                    CONF_WATER: True, CONF_SANITATION: True,
                    CONF_WASTE: True, CONF_TAXES: True,
                    CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
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

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            user_input[CONF_RESET_DAY] = int(user_input[CONF_RESET_DAY])
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(dict(self._entry.options))
        )
```

- [ ] **Step 4: Implement `__init__.py`**

```python
"""AGERE Water Price integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
```

- [ ] **Step 5: Write `strings.json` and `translations/en.json`**

Both files identical content:
```json
{
  "config": {
    "step": {
      "user": {
        "title": "AGERE Water Price",
        "description": "Pick the water-meter sensor (m³) and the billing-cycle reset day.",
        "data": {
          "source_entity": "Water meter sensor (m³)",
          "reset_day": "Billing-cycle reset day (1-28)"
        }
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "AGERE Water Price options",
        "data": {
          "reset_day": "Billing-cycle reset day (1-28)",
          "enable_water": "Include water charges",
          "enable_sanitation": "Include sanitation charges",
          "enable_waste": "Include waste charges",
          "enable_taxes": "Include state taxes",
          "include_vat": "Include VAT",
          "vat_rate": "VAT rate (e.g. 0.06)"
        }
      }
    }
  }
}
```

- [ ] **Step 6: Verify locally (HA harness absent)**

Run: `pytest -q`
Expected: engine tests pass; `test_config_flow.py` shows as **skipped** (not errored).
Also confirm each new module parses:
```bash
for f in custom_components/agere_water/__init__.py custom_components/agere_water/config_flow.py; do python -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" && echo "OK $f"; done
```
Expected: `OK` for both. And `python -c "import json; json.load(open('custom_components/agere_water/strings.json')); json.load(open('custom_components/agere_water/translations/en.json'))"` exits 0.

- [ ] **Step 7: Commit**

```bash
git add custom_components/agere_water tests/test_config_flow.py
git commit -m "feat: add config flow, options flow, and entry setup"
```

---

## Task 8: Sensor entities with recompute and persistence

**Files:**
- Create: `custom_components/agere_water/sensor.py`
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes: `calcular`, `marginal_price`, `Breakdown` (calculator); `CycleManager`, `CycleState` (cycle); `CalcConfig`, `Tariff`, all `CONF_*`, `DOMAIN` (const).
- Produces: `async_setup_entry(hass, entry, async_add_entities)` creating: `AgereTotalCostSensor`, `AgereMarginalPriceSensor`, `AgereCycleConsumptionSensor`, and one `AgereComponentCostSensor` per enabled component (water/sanitation/waste/taxes). A private `_AgereData` helper builds `CalcConfig` from entry options, owns the `CycleManager`, persists `CycleState` via `homeassistant.helpers.storage.Store` (key `agere_water_<entry_id>`), recomputes the latest `Breakdown` on source state changes, and notifies entities.

- [ ] **Step 1: Write failing test**

```python
from decimal import Decimal

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_RESET_DAY, CONF_SANITATION, CONF_SOURCE,
    CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)


async def _setup(hass: HomeAssistant, meter_state: str) -> None:
    hass.states.async_set("sensor.water_meter_total", meter_state,
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_RESET_DAY: 13, CONF_WATER: True, CONF_SANITATION: True,
            CONF_WASTE: True, CONF_TAXES: True, CONF_INCLUDE_VAT: True,
            CONF_VAT_RATE: "0.06",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensors_created(hass: HomeAssistant):
    await _setup(hass, "2620")
    assert hass.states.get("sensor.agere_total_cost") is not None
    assert hass.states.get("sensor.agere_marginal_price") is not None
    assert hass.states.get("sensor.agere_cycle_consumption") is not None
    assert hass.states.get("sensor.agere_water_cost") is not None


async def test_total_cost_recomputes_on_source_change(hass: HomeAssistant):
    await _setup(hass, "2620")            # baseline captured at 2620
    hass.states.async_set("sensor.water_meter_total", "2648",
                          {"unit_of_measurement": "m³"})  # +28 m³
    await hass.async_block_till_done()
    cost = hass.states.get("sensor.agere_total_cost")
    # 28 m³ this cycle; days depend on test clock, but cost must be > fixed charges (~15€)
    assert Decimal(cost.state) > Decimal("15")
    assert hass.states.get("sensor.agere_cycle_consumption").state == "28"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sensor.py -v`
Expected (local, no HA harness): the module is **skipped** via `importorskip`. That is the expected local state — red→green for this test runs on the user's HA / CI. Proceed to implement.

- [ ] **Step 3: Implement `sensor.py`**

```python
"""Sensor platform for AGERE Water Price."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .calculator import calcular, marginal_price
from .const import (
    CONF_INCLUDE_VAT, CONF_RESET_DAY, CONF_SANITATION, CONF_SOURCE,
    CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DEFAULT_RESET_DAY,
    DOMAIN, CalcConfig, Tariff,
)
from .cycle import CycleManager, CycleState

_STORE_VERSION = 1


def _calc_config(options: dict) -> CalcConfig:
    try:
        vat_rate = Decimal(str(options.get(CONF_VAT_RATE, "0.06")))
    except InvalidOperation:
        vat_rate = Decimal("0.06")
    return CalcConfig(
        tariff=Tariff(),
        include_water=options.get(CONF_WATER, True),
        include_sanitation=options.get(CONF_SANITATION, True),
        include_waste=options.get(CONF_WASTE, True),
        include_taxes=options.get(CONF_TAXES, True),
        include_vat=options.get(CONF_INCLUDE_VAT, True),
        vat_rate=vat_rate,
    )


class _AgereData:
    """Owns cycle state, persistence, and the latest computed breakdown."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.source = entry.data[CONF_SOURCE]
        self.config = _calc_config(dict(entry.options))
        reset_day = int(entry.options.get(CONF_RESET_DAY, DEFAULT_RESET_DAY))
        self._store = Store(hass, _STORE_VERSION, f"{DOMAIN}_{entry.entry_id}")
        self._manager = CycleManager(reset_day)
        self.breakdown = None
        self.marginal = Decimal("0")
        self.consumption = Decimal("0")
        self._listeners: list = []

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self._manager = CycleManager(
                self._manager._reset_day,
                CycleState(
                    cycle_start=date.fromisoformat(stored["cycle_start"]),
                    baseline=Decimal(stored["baseline"]),
                ),
            )

    async def _async_save(self) -> None:
        st = self._manager.state
        if st is not None:
            await self._store.async_save(
                {"cycle_start": st.cycle_start.isoformat(), "baseline": str(st.baseline)}
            )

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
        today = dt_util.now().date()
        prev = self._manager.state
        self._manager.update(today, meter_total)
        self.consumption = self._manager.consumption(meter_total)
        days = self._manager.days_elapsed(today)
        self.breakdown = calcular(self.consumption, days, self.config)
        self.marginal = marginal_price(self.consumption, days, self.config)
        if self._manager.state is not prev:
            self.hass.async_create_task(self._async_save())
        for cb in self._listeners:
            cb()


async def async_setup_entry(hass, entry, async_add_entities):
    data = _AgereData(hass, entry)
    await data.async_load()

    entities: list[SensorEntity] = [
        AgereTotalCostSensor(data),
        AgereMarginalPriceSensor(data),
        AgereCycleConsumptionSensor(data),
    ]
    for key, attr, name in (
        (CONF_WATER, "water", "Water cost"),
        (CONF_SANITATION, "sanitation", "Sanitation cost"),
        (CONF_WASTE, "waste", "Waste cost"),
        (CONF_TAXES, "taxes", "Taxes cost"),
    ):
        if entry.options.get(key, True):
            entities.append(AgereComponentCostSensor(data, attr, name))

    async_add_entities(entities)

    @callback
    def _on_source_change(event: Event) -> None:
        data.recompute()

    data.recompute()  # initial
    entry.async_on_unload(
        async_track_state_change_event(hass, [data.source], _on_source_change)
    )


class _AgereBase(SensorEntity):
    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, data: _AgereData) -> None:
        self._data = data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.entry.entry_id)},
            name="AGERE Water Price",
            manufacturer="AGERE",
        )

    async def async_added_to_hass(self) -> None:
        self._data.add_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class AgereTotalCostSensor(_AgereBase):
    _attr_name = "AGERE total cost"
    _attr_unique_id_suffix = "total_cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_total_cost"
        self.entity_id = "sensor.agere_total_cost"

    @property
    def native_value(self):
        return float(self._data.breakdown.total) if self._data.breakdown else None

    @property
    def extra_state_attributes(self):
        bd = self._data.breakdown
        if not bd:
            return None
        return {
            "base_without_vat": float(bd.base_without_vat),
            "vat": float(bd.vat),
            "cycle_consumption_m3": float(self._data.consumption),
            "water": float(bd.water),
            "sanitation": float(bd.sanitation),
            "waste": float(bd.waste),
            "taxes": float(bd.taxes),
        }


class AgereMarginalPriceSensor(_AgereBase):
    _attr_name = "AGERE marginal price"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/m³"

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_marginal_price"
        self.entity_id = "sensor.agere_marginal_price"

    @property
    def native_value(self):
        return float(self._data.marginal)


class AgereCycleConsumptionSensor(_AgereBase):
    _attr_name = "AGERE cycle consumption"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "m³"

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_cycle_consumption"
        self.entity_id = "sensor.agere_cycle_consumption"

    @property
    def native_value(self):
        # integer m³ to match AGERE metering
        return int(self._data.consumption)


class AgereComponentCostSensor(_AgereBase):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"

    def __init__(self, data: _AgereData, attr: str, name: str) -> None:
        super().__init__(data)
        self._attr = attr
        self._attr_name = f"AGERE {name}"
        self._attr_unique_id = f"{data.entry.entry_id}_{attr}_cost"
        self.entity_id = f"sensor.agere_{attr}_cost"

    @property
    def native_value(self):
        bd = self._data.breakdown
        return float(getattr(bd, self._attr)) if bd else None
```

- [ ] **Step 4: Verify locally (HA harness absent)**

Run: `pytest -q`
Expected: engine tests pass; `test_sensor.py` shows as **skipped** (not errored).
Confirm the module parses:
```bash
python -c "import ast; ast.parse(open('custom_components/agere_water/sensor.py').read())" && echo OK
```
Expected: `OK`.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all engine tests pass (Tasks 1–6); `test_config_flow.py` and `test_sensor.py` skipped. Zero failures, zero errors.

- [ ] **Step 6: Commit**

```bash
git add custom_components/agere_water/sensor.py tests/test_sensor.py
git commit -m "feat: add sensor entities with recompute and cycle persistence"
```

---

## Task 9: README and HACS metadata

**Files:**
- Create: `README.md`
- Modify: `custom_components/agere_water/manifest.json` (nothing further; verify present)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `README.md`**

Include: what it does; the tariff table (from the spec); installation via HACS (custom repository) and manual copy to `config/custom_components/agere_water`; configuration steps (pick meter sensor, reset day); options (component toggles, VAT toggle + rate, reset day); the sensor list; and the Energy dashboard wiring:

> In **Settings → Dashboards → Energy → Water consumption**, add your meter source and choose **"Use an entity tracking the total costs"** → `sensor.agere_total_cost`. (The `sensor.agere_marginal_price` "current price" option is an incremental approximation and does not include fixed charges — prefer total cost.)

Note the known limitation: the first partial cycle after install counts consumption from install time (baseline captured then), so it under-reports until the first reset boundary.

- [ ] **Step 2: Commit**

```bash
git add README.md custom_components/agere_water/manifest.json
git commit -m "docs: add README with install, options, and Energy wiring"
```

---

## Self-Review

**Spec coverage:**
- Tariff structure + per-line rounding → Tasks 2–4 (validated against both invoices). ✓
- Tier proration by days → Task 2 (`tier_limits`), used in Tasks 3–5. ✓
- VAT toggle + rate, waste excluded from VAT → Task 4 (`test_vat_excluded`, `test_waste_never_taxed`). ✓
- Configurable components → Task 4 toggles + Task 8 per-component sensors. ✓
- Marginal price sensor → Task 5 + Task 8. ✓
- Total cost sensor + sub-costs + cycle consumption → Task 8. ✓
- Configurable reset day + cycle tracking + persistence → Tasks 6 (logic) + 8 (Store). ✓
- Config flow / options (English names) → Task 7. ✓
- Energy dashboard wiring + limitation note → Task 9. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step has concrete assertions with real expected values. ✓

**Type consistency:** `calcular`/`marginal_price`/`water_lines`/`tier_limits`/`money`/`price4` signatures identical across definition (Tasks 2–5) and use (Task 8). `CycleManager.update/consumption/days_elapsed/state` identical across Task 6 and Task 8. `CalcConfig`/`Tariff`/`Breakdown`/`TierLine`/`CycleState` field names consistent throughout. ✓

**Note on tariff-override keys:** `const.py` defines `CONF_TARIFF_*`/`CONF_WATER_TIER_*` keys for completeness, but the options flow (Task 7) and `_calc_config` (Task 8) use the default `Tariff()` and do not yet expose per-tariff-value editing in the UI. Editing tariff values in the UI is intentionally deferred (YAGNI for v0.1.0 — annual updates can ship as a code bump); the keys exist so a later task can wire them without a constants change.
