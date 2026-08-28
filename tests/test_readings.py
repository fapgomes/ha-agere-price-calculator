from datetime import date
from decimal import Decimal

import pytest

from custom_components.agere_water.readings import (
    SOURCE_AUTO, SOURCE_MANUAL, Cycle, Reading, ReadingLog, days_elapsed,
    is_overdue,
)

# Synthetic readings; day counts (28 and 33) exercise the tier proration.
READINGS = [
    Reading(date(2026, 6, 12), Decimal("500")),
    Reading(date(2026, 7, 10), Decimal("512")),
    Reading(date(2026, 8, 12), Decimal("536")),
]


def test_empty_log():
    log = ReadingLog()
    assert len(log) == 0
    assert log.last is None
    assert log.closed_cycles() == []
    assert log.current_cycle(Decimal("536")) is None


def test_readings_sorted_ascending():
    log = ReadingLog(reversed(READINGS))
    assert [r.date for r in log.readings] == [
        date(2026, 6, 12), date(2026, 7, 10), date(2026, 8, 12),
    ]
    assert log.last.m3 == Decimal("536")


def test_closed_cycles_derived_from_consecutive_readings():
    cycles = ReadingLog(READINGS).closed_cycles()
    assert cycles == [
        Cycle(date(2026, 6, 13), date(2026, 7, 10), 28, Decimal("12"), False),
        Cycle(date(2026, 7, 11), date(2026, 8, 12), 33, Decimal("24"), False),
    ]


def test_current_cycle_with_next_reading_date():
    log = ReadingLog(READINGS)
    cycle = log.current_cycle(Decimal("543"), date(2026, 9, 3))
    assert cycle == Cycle(
        date(2026, 8, 13), date(2026, 9, 3), 22, Decimal("7"), False
    )


def test_current_cycle_estimates_from_previous_cycle():
    log = ReadingLog(READINGS)
    cycle = log.current_cycle(Decimal("543"))
    # previous closed cycle was 33 days -> 08-13 .. 09-14
    assert cycle.start == date(2026, 8, 13)
    assert cycle.end == date(2026, 9, 14)
    assert cycle.days == 33
    assert cycle.estimated is True


def test_current_cycle_single_reading_defaults_to_30_days():
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("536"), SOURCE_AUTO)])
    cycle = log.current_cycle(Decimal("538"))
    assert cycle.days == 30
    assert cycle.end == date(2026, 9, 11)
    assert cycle.consumption == Decimal("2")
    assert cycle.estimated is True


def test_current_cycle_floors_consumption_when_meter_decreases():
    log = ReadingLog(READINGS)
    cycle = log.current_cycle(Decimal("480"))  # meter replaced / reset
    assert cycle.consumption == Decimal("0")


def test_next_reading_date_before_cycle_start_rejected():
    log = ReadingLog(READINGS)
    with pytest.raises(ValueError, match="after"):
        log.current_cycle(Decimal("543"), date(2026, 8, 12))


def test_set_inserts_in_order():
    log = ReadingLog([READINGS[0], READINGS[2]])
    log = log.set(READINGS[1])
    assert [r.date for r in log.readings] == [
        date(2026, 6, 12), date(2026, 7, 10), date(2026, 8, 12),
    ]


def test_set_replaces_same_date():
    log = ReadingLog(READINGS).set(
        Reading(date(2026, 8, 12), Decimal("537"))
    )
    assert len(log) == 3
    assert log.last.m3 == Decimal("537")


def test_set_is_immutable():
    original = ReadingLog(READINGS)
    original.set(Reading(date(2026, 9, 3), Decimal("545")))
    assert len(original) == 3


def test_remove_existing():
    log = ReadingLog(READINGS).remove(date(2026, 7, 10))
    assert [r.date for r in log.readings] == [date(2026, 6, 12), date(2026, 8, 12)]
    assert log.closed_cycles()[0].days == 61


def test_remove_missing_raises():
    with pytest.raises(ValueError, match="no reading"):
        ReadingLog(READINGS).remove(date(2026, 1, 1))


def test_remove_last_reading_empties_log():
    log = ReadingLog([Reading(date(2026, 8, 12), Decimal("536"))])
    assert len(log.remove(date(2026, 8, 12))) == 0


def test_duplicate_dates_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        ReadingLog([
            Reading(date(2026, 8, 12), Decimal("536")),
            Reading(date(2026, 8, 12), Decimal("537")),
        ])


def test_decreasing_m3_rejected():
    with pytest.raises(ValueError, match="lower than"):
        ReadingLog([
            Reading(date(2026, 7, 10), Decimal("512")),
            Reading(date(2026, 8, 12), Decimal("480")),
        ])


def test_decreasing_m3_error_names_both_readings():
    """The message is surfaced verbatim in the UI, so it must say which two
    readings conflict and with what values."""
    with pytest.raises(ValueError) as excinfo:
        ReadingLog([
            Reading(date(2025, 1, 13), Decimal("12.0")),
            Reading(date(2025, 2, 12), Decimal("11")),
        ])
    message = str(excinfo.value)
    for fragment in ("2025-02-12", "11", "2025-01-13", "12.0"):
        assert fragment in message


def test_negative_m3_rejected():
    with pytest.raises(ValueError, match="negative"):
        ReadingLog([Reading(date(2026, 8, 12), Decimal("-1"))])


def test_set_that_breaks_ordering_rejected():
    log = ReadingLog(READINGS)
    with pytest.raises(ValueError, match="lower than"):
        log.set(Reading(date(2026, 7, 10), Decimal("700")))


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
    assert Reading(date(2026, 8, 12), Decimal("536")).source == SOURCE_MANUAL
