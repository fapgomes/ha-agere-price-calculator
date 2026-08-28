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
