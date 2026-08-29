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
    {"date": "2026-07-10", "m3": "512", "source": "manual"},
    {"date": "2026-08-12", "m3": "536", "source": "auto"},
]


def test_readings_from_options_roundtrip():
    log = readings_from_options({CONF_READINGS: STORED})
    assert [r.date for r in log.readings] == [date(2026, 7, 10), date(2026, 8, 12)]
    assert log.last.m3 == Decimal("536")
    assert log.last.source == SOURCE_AUTO
    assert readings_to_options(log) == STORED


def test_readings_from_options_missing_key():
    assert len(readings_from_options({})) == 0
    assert len(readings_from_options({CONF_READINGS: None})) == 0


def test_readings_from_options_defaults_source_to_manual():
    log = readings_from_options({CONF_READINGS: [{"date": "2026-08-12", "m3": "536"}]})
    assert log.last.source == SOURCE_MANUAL


def test_readings_from_options_accepts_numeric_m3():
    """Values written by a service call arrive as float/int, not str."""
    log = readings_from_options({CONF_READINGS: [{"date": "2026-08-12", "m3": 536}]})
    assert log.last.m3 == Decimal("536")


def test_readings_from_options_rejects_malformed_date():
    with pytest.raises(ValueError):
        readings_from_options({CONF_READINGS: [{"date": "12-08-2026", "m3": "536"}]})


def test_readings_from_options_propagates_log_validation():
    with pytest.raises(ValueError, match="lower than"):
        readings_from_options({CONF_READINGS: [
            {"date": "2026-07-10", "m3": "512"},
            {"date": "2026-08-12", "m3": "480"},
        ]})


def test_readings_to_options_stores_m3_as_string():
    """String, not float: options are JSON, and a float would lose precision on
    the way back to Decimal. Trailing zeros are trimmed, so 536.500 stores as
    "536.5" and round-trips to the same value."""
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("536.500"))])
    stored = readings_to_options(log)
    assert stored == [{"date": "2026-08-12", "m3": "536.5", "source": "manual"}]
    assert isinstance(stored[0]["m3"], str)
    assert readings_from_options({CONF_READINGS: stored}).last.m3 == Decimal("536.5")


def test_next_reading_date_from_options():
    assert next_reading_date_from_options(
        {CONF_NEXT_READING_DATE: "2026-09-03"}
    ) == date(2026, 9, 3)
    assert next_reading_date_from_options({}) is None
    assert next_reading_date_from_options({CONF_NEXT_READING_DATE: None}) is None
    assert next_reading_date_from_options({CONF_NEXT_READING_DATE: ""}) is None


def test_readings_to_options_drops_float_formatting():
    """m3 arrives as a float from the service schema and the number selector, so
    543 becomes Decimal("543.0"). Storing that would show "543.0 m³" in the UI."""
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("543.0"))])
    assert readings_to_options(log)[0]["m3"] == "543"


def test_readings_to_options_never_uses_exponent_notation():
    """Decimal.normalize() turns 500.0 into 5E+2, which would be stored and shown
    verbatim."""
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("500.0"))])
    assert readings_to_options(log)[0]["m3"] == "500"


def test_readings_to_options_trims_only_trailing_zeros():
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("543.60"))])
    assert readings_to_options(log)[0]["m3"] == "543.6"


def test_readings_to_options_keeps_full_precision():
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("536.61791992188"))])
    assert readings_to_options(log)[0]["m3"] == "536.61791992188"


# --- tariff schedule ---

from custom_components.agere_water.const import (  # noqa: E402
    CONF_TARIFFS, CONF_TARIFFS_SEEDED_THROUGH,
)
from custom_components.agere_water.entry_options import (  # noqa: E402
    seeded_through_from_options, tariffs_from_options, tariffs_to_options,
)
from custom_components.agere_water.tariffs import BUILTIN_SCHEDULE  # noqa: E402

STORED_TARIFF = {
    "effective_from": "2026-02-01",
    "water_tier_bounds": [5, 10, 15, 25],
    "water_tier_prices": ["0.508", "0.6636", "0.8605", "1.8765", "2.6852"],
    "water_availability": "4.8623",
    "sanitation_drainage": "0.4809",
    "sanitation_availability": "4.8766",
    "waste_variable": "0.0147",
    "waste_fixed": "2.5257",
    "tax_water": "0.0382",
    "tax_sanitation": "0.015",
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
    assert t.water_tier_prices[0] == Decimal("0.508")
    assert t.tax_waste_mgmt == Decimal("2.8821")
    assert tariffs_to_options(schedule) == [STORED_TARIFF]


def test_tariffs_roundtrip_the_builtin_schedule():
    stored = tariffs_to_options(BUILTIN_SCHEDULE)
    rebuilt = tariffs_from_options({CONF_TARIFFS: stored})
    assert len(rebuilt) == len(BUILTIN_SCHEDULE)
    for a, b in zip(rebuilt.periods, BUILTIN_SCHEDULE.periods):
        assert a.effective_from == b.effective_from
        assert a.tariff.water_availability == b.tariff.water_availability
        assert a.tariff.water_tier_prices == b.tariff.water_tier_prices


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
    """Same formatter as the readings path: values typed as 2.50 store as 2.5."""
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
