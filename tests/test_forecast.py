from datetime import date
from decimal import Decimal

from custom_components.agere_water.forecast import project_consumption
from custom_components.agere_water.readings import Cycle

CLOSED = [
    Cycle(date(2026, 6, 13), date(2026, 7, 10), 28, Decimal("12"), False),
    Cycle(date(2026, 7, 11), date(2026, 8, 12), 33, Decimal("24"), False),
]
CURRENT = Cycle(date(2026, 8, 13), date(2026, 9, 3), 22, Decimal("7"), False)


def test_blend_weights_current_rate_by_progress():
    """15/22 through the period: 15/22 of the current rate (7/15) plus 7/22 of
    the historical average (36 m3 / 61 days)."""
    assert project_consumption(CURRENT, 15, CLOSED) == Decimal("11.131")


def test_day_one_damps_a_heavy_first_day():
    """3 m3 on day 1 is 5x the historical rate. Extrapolating it naively gives
    66 m3; the blend stays within a quarter of the historical projection."""
    early = Cycle(CURRENT.start, CURRENT.end, 22, Decimal("3"), False)
    projected = project_consumption(early, 1, CLOSED)
    naive = Decimal("3") * 22
    historical_only = Decimal("36") / Decimal("61") * 22
    assert projected < historical_only * Decimal("1.25")
    assert projected < naive / 4


def test_last_day_equals_the_metered_consumption():
    """Weight is 1, so the projection is the current rate over the full period,
    which is exactly what has been metered."""
    full = Cycle(CURRENT.start, CURRENT.end, 22, Decimal("11"), False)
    assert project_consumption(full, 22, full and CLOSED) == Decimal("11.000")


def test_never_projects_below_what_is_already_metered():
    """A period that already used far more than history would predict must not
    forecast a lower total than the meter already shows."""
    heavy = Cycle(CURRENT.start, CURRENT.end, 22, Decimal("30"), False)
    assert project_consumption(heavy, 2, CLOSED) >= Decimal("30")


def test_no_history_uses_the_current_rate_alone():
    assert project_consumption(CURRENT, 11, []) == Decimal("14.000")


def test_no_history_and_no_elapsed_days_projects_what_is_metered():
    assert project_consumption(CURRENT, 0, []) == Decimal("7.000")
