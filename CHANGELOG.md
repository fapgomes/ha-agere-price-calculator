# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- `sensor.agere_forecast` dropped by about 0.70 € at every midnight and climbed
  back through the day. The projection counted elapsed days as whole numbers, so
  the remaining days fell by one the instant the date changed while the metered
  consumption had grown only by that day's actual use. Elapsed time is now
  continuous, and the curve is smooth.

## [0.2.0] - 2026-08-28

### Changed
- **Breaking:** billing periods now come from meter readings instead of a fixed
  reset day, matching AGERE's own read-to-read periods. Existing entries migrate
  automatically: the previous cycle boundary and baseline are converted into an
  equivalent reading, so no consumption is lost.
- **Breaking:** the minimum Home Assistant version is now 2024.3.0, up from
  2024.1.0. Migrating a config entry uses the `version` parameter of
  `async_update_entry`, which was introduced in 2024.3.0 — 2024.2.0 does not
  accept it.
- The integration's **Configure** dialog is now a menu — *Readings*, *Next
  reading date* and *Charges and VAT* — instead of a single form. The initial
  setup form only asks for the meter sensor.

### Added
- Reading log with `agere_water.set_reading`, `agere_water.remove_reading` and
  `agere_water.set_next_reading_date` services, and a Readings section under the
  integration's Configure menu for editing past periods.
- `sensor.agere_last_invoice`, exposing every reconstructed billing period.
- `sensor.agere_forecast`, projecting the total for the period in progress. The
  projection blends the period's own rate with the historical average, weighted
  by how far into the period it is, so it is usable from day one instead of
  swinging wildly.
- `sensor.agere_total_cost` gained attributes describing the period it is
  billing: `cycle_start`, `cycle_end`, `billing_days`,
  `billing_days_estimated`, `cycle_overdue` and `next_reading_date`.
- Test suite now runs in CI on Python 3.13.

### Fixed
- Monetary sensors declared `state_class: total_increasing`, which Home
  Assistant rejects for `device_class: monetary` (only `total` is allowed). The
  five cost sensors logged a warning on every start and, more importantly, got
  no long-term statistics — the series the Energy dashboard reads. They now use
  `state_class: total` with `last_reset` set to the start of the billing period.
- Rejecting a reading in the Configure dialog now says which two readings
  conflict and with what values, instead of a generic "invalid" message. The
  previous wording gave no hint when the invoice's `Consumo` had been entered
  where its `Leitura` belongs.
- Period length no longer assumes a calendar month. A period whose meter
  readings are 33 days apart was being computed as a 31-day calendar month,
  which prorates the consumption tiers wrongly and overstated the total by more
  than a euro.

## [0.1.2] - 2026-07-27

### Added
- Brand icon and logo (water drop + euro mark) shipped in
  `custom_components/agere_water/brand/`, served locally by Home Assistant
  2026.3.0+ (older versions ignore the folder). Includes dark-theme logo
  variants and the generator script under `assets/brand/`.

## [0.1.1] - 2026-07-20

### Changed
- `sensor.agere_cycle_consumption` now reports fractional m³ (litre precision,
  3 decimals) instead of truncating to whole m³, so partial usage is visible
  as it accrues. Cost calculations were already based on the fractional value;
  only the displayed consumption changed.

## [0.1.0] - 2026-07-20

Initial release.

### Added
- AGERE Doméstico water-bill calculation engine (`calculator.py`), pure and
  Decimal-based, validated to the cent against two real 2026 invoices
  (28 m³/30 days → 71.21 €; 18 m³/28 days → 44.21 €).
- Tiered water pricing (5 escalões) with fixed availability, sanitation
  (drainage + availability), waste (variable + fixed), and state taxes.
- 6% VAT applied to water, sanitation, and water-resource taxes; waste and
  the waste-management tax excluded (art. 2, nº2 CIVA).
- Billing-cycle manager (`cycle.py`) with a configurable monthly reset day,
  meter baseline, cycle rollover, and `Store`-backed persistence.
- Tier limits prorated by the fixed length of the current cycle, so
  `total_cost` is monotonic in consumption and still matches the invoice at
  cycle close.
- Home Assistant sensors: `sensor.agere_total_cost`,
  `sensor.agere_marginal_price`, `sensor.agere_cycle_consumption`, and
  per-component costs (`water`/`sanitation`/`waste`/`taxes`).
- Config flow (UI) and options: source meter entity, reset day, per-component
  toggles, and VAT toggle + rate.
- Daily timer that rolls the billing cycle even when the meter is silent.
- HACS metadata and README with install/deploy and Energy-dashboard wiring.

### Known limitations
- The first billing cycle after install is partial (baseline captured at
  install time), under-reporting until the next reset boundary.
- Editing tariff values from the UI is not yet exposed; a tariff update
  requires a code change (AGERE updates its tariff annually).

[0.1.2]: https://github.com/fapgomes/ha-agere-price-calculator/releases/tag/v0.1.2
[0.1.1]: https://github.com/fapgomes/ha-agere-price-calculator/releases/tag/v0.1.1
[0.1.0]: https://github.com/fapgomes/ha-agere-price-calculator/releases/tag/v0.1.0
