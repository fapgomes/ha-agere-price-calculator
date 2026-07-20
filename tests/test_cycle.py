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


def test_consumption_floored_when_meter_decreases():
    mgr = CycleManager(reset_day=13)
    mgr.update(date(2026, 7, 20), Decimal("2620"))
    # meter reads LOWER than baseline (e.g. meter replacement/reset) -> floored at 0
    assert mgr.consumption(Decimal("2610")) == Decimal("0")


def test_restore_from_existing_state_no_roll():
    state = CycleState(cycle_start=date(2026, 7, 13), baseline=Decimal("2600"))
    mgr = CycleManager(reset_day=13, state=state)
    st = mgr.update(date(2026, 7, 25), Decimal("2615"))  # same cycle -> no roll
    assert st.cycle_start == date(2026, 7, 13)
    assert st.baseline == Decimal("2600")
    assert mgr.consumption(Decimal("2615")) == Decimal("15")


def test_restore_from_existing_state_then_roll():
    state = CycleState(cycle_start=date(2026, 7, 13), baseline=Decimal("2600"))
    mgr = CycleManager(reset_day=13, state=state)
    st = mgr.update(date(2026, 8, 20), Decimal("2650"))  # crossed into new cycle -> roll
    assert st.cycle_start == date(2026, 8, 13)
    assert st.baseline == Decimal("2650")


def test_consumption_and_days_before_first_update():
    mgr = CycleManager(reset_day=13)
    assert mgr.consumption(Decimal("2600")) == Decimal("0")
    assert mgr.days_elapsed(date(2026, 7, 20)) == 1


def test_reset_day_property_exposed():
    assert CycleManager(reset_day=13).reset_day == 13
