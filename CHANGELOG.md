# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.1]: https://github.com/fapgomes/ha-agere-price-calculator/releases/tag/v0.1.1
[0.1.0]: https://github.com/fapgomes/ha-agere-price-calculator/releases/tag/v0.1.0
