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
