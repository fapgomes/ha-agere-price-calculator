# AGERE Water Price

A Home Assistant custom integration that turns a water-meter reading (m³)
into real AGERE Doméstico (Braga) billing costs — tiered water price, fixed
availability/drainage charges, waste, government taxes, and VAT — so you can
feed accurate water costs into the HA **Energy** dashboard or your own
dashboards.

## What it does

You already have a water-meter sensor in Home Assistant reporting cumulative
consumption in m³ (e.g. a smart meter or pulse counter). AGERE's tariff isn't
a flat €/m³ price: it's a set of consumption tiers, fixed period charges,
non-taxed waste fees, and government taxes, with 6% VAT applied to some
components but not others. A single "price per m³" can't represent that.

This integration tracks your billing cycle (a configurable reset day each
month), computes the AGERE bill breakdown for the consumption in the current
cycle, and exposes it as sensors — including a running total-cost sensor
that matches what AGERE actually bills, to the cent.

## Tariff (2026 Doméstico defaults)

| Component | Value | VAT (6%) |
|---|---|---|
| Water, 0–5 m³ | 0.5080 €/m³ | yes |
| Water, 5–10 m³ | 0.6636 €/m³ | yes |
| Water, 10–15 m³ | 0.8605 €/m³ | yes |
| Water, 15–25 m³ | 1.8765 €/m³ | yes |
| Water, >25 m³ | 2.6852 €/m³ | yes |
| Water availability (fixed/cycle) | 4.8623 € | yes |
| Sanitation drainage | 0.4809 €/m³ | yes |
| Sanitation availability (fixed/cycle) | 4.8766 € | yes |
| Waste, variable | 0.0147 €/m³ | **no** |
| Waste, fixed (fixed/cycle) | 2.5257 € | **no** |
| Tax — water resources | 0.0382 €/m³ | yes |
| Tax — sanitation resources | 0.0150 €/m³ | yes |
| Tax — waste management (fixed/cycle) | 2.8821 € | **no** |

VAT (default 6%, configurable) applies to water (tiers + availability),
sanitation (drainage + availability), and the two resource taxes. It never
applies to waste (variable, fixed, or the waste-management tax) — that
exemption comes from Portuguese law (art. 2, nº2 CIVA).

The water tier *limits* (5/10/15/25 m³) are for a 30-day cycle and are
prorated by the number of days actually elapsed in your cycle
(`round(limit × days / 30)`), matching how AGERE bills cycles that aren't
exactly 30 days. Fixed charges (availability, waste fixed, waste-management
tax) are billed in full per cycle, not prorated.

These are the integration's built-in *defaults* — AGERE updates its tariff
annually, so treat these values as a snapshot for 2026, not a permanent
guarantee. Per-tariff-value editing in the UI is not yet exposed (see
Limitations below); a tariff update currently requires a code change.

## Installation

### HACS (custom repository)

1. In HACS, open the overflow menu → **Custom repositories**.
2. Add this repository's URL, category **Integration**.
3. Find "AGERE Water Price" in HACS and install it.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/agere_water/` into your HA config directory, at
   `config/custom_components/agere_water/`.
2. Restart Home Assistant.

## Configuration

Go to **Settings → Devices & Services → Add Integration**, search for
"AGERE Water Price", and set:

- **Source entity** — the sensor providing your cumulative water-meter
  reading in m³.
- **Reset day** — the day of the month (1–28) your AGERE billing cycle
  resets.

### Options

Available afterwards via **Configure** on the integration entry:

- **Reset day** — the billing-cycle reset day (1–28).
- **Enable water / sanitation / waste / taxes** — independent toggles for
  each tariff component; disabled components are excluded from the total
  and from VAT.
- **Include VAT** — toggle VAT on or off.
- **VAT rate** — default `0.06` (6%).

## Sensors

| Entity ID | Description |
|---|---|
| `sensor.agere_total_cost` | Running total cost (EUR) for the current billing cycle — all enabled components plus VAT. Attributes include the base (pre-VAT), the VAT amount, cycle consumption, and each component's cost. |
| `sensor.agere_marginal_price` | Cost of the next cubic metre (EUR/m³) at the current point in the cycle — the active water tier's rate plus any enabled variable components and VAT. |
| `sensor.agere_cycle_consumption` | Consumption (m³) accumulated so far in the current billing cycle. |
| `sensor.agere_water_cost` | Water sub-cost (tiers + availability), when water is enabled. |
| `sensor.agere_sanitation_cost` | Sanitation sub-cost (drainage + availability), when sanitation is enabled. |
| `sensor.agere_waste_cost` | Waste sub-cost (variable + fixed, never VAT), when waste is enabled. |
| `sensor.agere_taxes_cost` | Government-taxes sub-cost, when taxes is enabled. |

Per-component sensors are only created for components that are enabled in
the options.

## Energy dashboard

In **Settings → Dashboards → Energy → Water consumption**, add your
meter source, then choose **"Use an entity tracking the total costs"** and
select `sensor.agere_total_cost`.

`sensor.agere_marginal_price` is also offered as a "current price" option,
but it is only an incremental approximation of the active water tier — it
does **not** include the fixed availability/waste/tax charges billed per
cycle. Prefer `sensor.agere_total_cost` for accurate costs.

## Known limitation

The first billing cycle after installing the integration is partial: the
consumption baseline is captured at install time (or first restart), not at
the actual start of your current AGERE cycle. As a result, that first cycle
under-reports consumption and cost until the next reset-day boundary, after
which tracking is accurate for every full cycle.

## Accuracy

The calculation engine is validated to the cent against two real AGERE
invoices:

- 28 m³ over 30 days → 71.21 € total.
- 18 m³ over 28 days → 44.21 € total (tier limits prorated to 5/9/14/23 m³
  for the 28-day cycle).
