from datetime import date
from decimal import Decimal

import pytest

from custom_components.agere_water.tariffs import (
    BUILTIN_SCHEDULE, FIXED_COMPONENTS, VARIABLE_COMPONENTS, Tariff,
    TariffPeriod, TariffSchedule,
)


def test_builtin_has_the_four_known_effective_dates():
    assert [p.effective_from for p in BUILTIN_SCHEDULE.periods] == [
        date(2024, 12, 12), date(2025, 1, 1), date(2026, 1, 1), date(2026, 2, 1),
    ]


def test_builtin_snapshots_carry_the_full_value_set_forward():
    """Each snapshot is complete, not a delta: 2025-01-01 changes only the two
    resource taxes and must keep every other value from the base."""
    base = BUILTIN_SCHEDULE.at(date(2024, 12, 12))
    taxes = BUILTIN_SCHEDULE.at(date(2025, 1, 1))
    assert taxes.tax_water == Decimal("0.0382")
    assert taxes.tax_sanitation == Decimal("0.0150")
    assert taxes.water_tier_prices == base.water_tier_prices
    assert taxes.water_availability == base.water_availability
    assert taxes.tax_waste_mgmt == base.tax_waste_mgmt


def test_builtin_2026_02_01_is_the_current_tariff():
    t = BUILTIN_SCHEDULE.at(date(2026, 8, 12))
    assert t.water_tier_prices == (
        Decimal("0.5080"), Decimal("0.6636"), Decimal("0.8605"),
        Decimal("1.8765"), Decimal("2.6852"),
    )
    assert t.water_availability == Decimal("4.8623")
    assert t.sanitation_drainage == Decimal("0.4809")
    assert t.sanitation_availability == Decimal("4.8766")
    assert t.waste_variable == Decimal("0.0147")
    assert t.waste_fixed == Decimal("2.5257")
    assert t.tax_waste_mgmt == Decimal("2.8821")


def test_builtin_knows_every_water_tier():
    """The >25 m3 tier is absent from every invoice on hand — no period reached
    it — so it came from AGERE's published tariff sheets instead."""
    assert BUILTIN_SCHEDULE.at(date(2025, 6, 1)).water_tier_prices[4] == Decimal("2.5114")
    assert BUILTIN_SCHEDULE.at(date(2026, 2, 1)).water_tier_prices[4] == Decimal("2.6852")
    for period in BUILTIN_SCHEDULE.periods:
        assert None not in period.tariff.water_tier_prices


def test_a_schedule_may_still_carry_an_unknown_price():
    """The capability stays: a user adding an older tariff can leave a price
    empty rather than guess it."""
    unknown = TariffSchedule([TariffPeriod(
        date(2020, 1, 1), _tariff(water_tier_prices=(Decimal("1"),) * 4 + (None,))
    )])
    assert unknown.at(date(2020, 6, 1)).water_tier_prices[4] is None


def test_at_picks_the_tariff_in_force():
    assert BUILTIN_SCHEDULE.at(date(2025, 12, 31)).tax_waste_mgmt == Decimal("2.4260")
    assert BUILTIN_SCHEDULE.at(date(2026, 1, 1)).tax_waste_mgmt == Decimal("2.8821")
    assert BUILTIN_SCHEDULE.at(date(2026, 1, 31)).water_availability == Decimal("4.5476")
    assert BUILTIN_SCHEDULE.at(date(2026, 2, 1)).water_availability == Decimal("4.8623")


def test_at_refuses_dates_before_the_earliest_snapshot():
    """Applying 2024 prices to a 2023 invoice would look plausible and be wrong."""
    with pytest.raises(ValueError, match="2024-12-12"):
        BUILTIN_SCHEDULE.at(date(2023, 5, 1))


def test_change_dates_for_water_ignores_the_tax_only_change():
    """This is the whole mechanism: on 2025-01-01 only the taxes moved, so water
    must stay a single line over the full period."""
    assert BUILTIN_SCHEDULE.change_dates_for(
        "water", date(2024, 12, 20), date(2025, 1, 15)
    ) == []
    assert BUILTIN_SCHEDULE.change_dates_for(
        "tax_water", date(2024, 12, 20), date(2025, 1, 15)
    ) == [date(2025, 1, 1)]


def test_change_dates_for_water_sees_the_2026_02_change():
    assert BUILTIN_SCHEDULE.change_dates_for(
        "water", date(2026, 1, 20), date(2026, 2, 15)
    ) == [date(2026, 2, 1)]
    assert BUILTIN_SCHEDULE.change_dates_for(
        "tax_water", date(2026, 1, 20), date(2026, 2, 15)
    ) == []


def test_change_dates_for_excludes_the_period_start():
    """A change landing exactly on the first day needs no split: the whole period
    is already on the new tariff."""
    assert BUILTIN_SCHEDULE.change_dates_for(
        "water", date(2026, 2, 1), date(2026, 2, 28)
    ) == []


def test_change_dates_for_includes_the_period_end():
    assert BUILTIN_SCHEDULE.change_dates_for(
        "tax_waste_mgmt", date(2025, 12, 20), date(2026, 1, 1)
    ) == [date(2026, 1, 1)]


def test_component_groups_cover_every_tariff_field():
    fields = set(Tariff.__dataclass_fields__) - {"water_tier_bounds", "water_tier_prices"}
    assert set(VARIABLE_COMPONENTS) | set(FIXED_COMPONENTS) == fields | {"water"}
    assert not set(VARIABLE_COMPONENTS) & set(FIXED_COMPONENTS)


def _tariff(**kw):
    base = dict(
        water_tier_bounds=(5, 10, 15, 25),
        water_tier_prices=(Decimal("1"),) * 5,
        water_availability=Decimal("1"), sanitation_drainage=Decimal("1"),
        sanitation_availability=Decimal("1"), waste_variable=Decimal("1"),
        waste_fixed=Decimal("1"), tax_water=Decimal("1"),
        tax_sanitation=Decimal("1"), tax_waste_mgmt=Decimal("1"),
    )
    base.update(kw)
    return Tariff(**base)


def test_schedule_sorts_and_rejects_duplicate_dates():
    a = TariffPeriod(date(2026, 1, 1), _tariff())
    b = TariffPeriod(date(2025, 1, 1), _tariff())
    assert [p.effective_from for p in TariffSchedule([a, b]).periods] == [
        date(2025, 1, 1), date(2026, 1, 1),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        TariffSchedule([a, TariffPeriod(date(2026, 1, 1), _tariff())])


def test_schedule_rejects_an_empty_calendar():
    with pytest.raises(ValueError, match="at least one"):
        TariffSchedule([])


def test_schedule_rejects_negative_prices():
    with pytest.raises(ValueError, match="negative"):
        TariffSchedule([TariffPeriod(date(2026, 1, 1),
                                     _tariff(waste_fixed=Decimal("-1")))])


def test_schedule_rejects_bounds_that_do_not_increase():
    with pytest.raises(ValueError, match="increasing"):
        TariffSchedule([TariffPeriod(date(2026, 1, 1),
                                     _tariff(water_tier_bounds=(5, 5, 15, 25)))])


def test_schedule_rejects_bounds_of_the_wrong_length():
    with pytest.raises(ValueError, match="one fewer"):
        TariffSchedule([TariffPeriod(date(2026, 1, 1),
                                     _tariff(water_tier_bounds=(5, 10, 15)))])


def test_set_upserts_by_date_and_remove_drops():
    s = TariffSchedule([TariffPeriod(date(2026, 1, 1), _tariff())])
    s = s.set(TariffPeriod(date(2026, 1, 1), _tariff(waste_fixed=Decimal("2"))))
    assert len(s) == 1
    assert s.at(date(2026, 1, 1)).waste_fixed == Decimal("2")
    s = s.set(TariffPeriod(date(2026, 6, 1), _tariff()))
    assert len(s.remove(date(2026, 6, 1))) == 1
    with pytest.raises(ValueError, match="no tariff"):
        s.remove(date(2020, 1, 1))


def test_merge_newer_adds_only_dates_after_the_mark():
    stored = TariffSchedule([TariffPeriod(date(2024, 12, 12), _tariff())])
    merged, mark = stored.merge_newer(BUILTIN_SCHEDULE, date(2025, 1, 1))
    assert [p.effective_from for p in merged.periods] == [
        date(2024, 12, 12), date(2026, 1, 1), date(2026, 2, 1),
    ]
    assert mark == date(2026, 2, 1)


def test_merge_newer_never_overwrites_a_stored_snapshot():
    edited = TariffSchedule([TariffPeriod(date(2026, 2, 1),
                                          _tariff(waste_fixed=Decimal("9")))])
    merged, _ = edited.merge_newer(BUILTIN_SCHEDULE, date(2026, 2, 1))
    assert merged.at(date(2026, 2, 1)).waste_fixed == Decimal("9")


def test_merge_newer_seeds_everything_when_unmarked():
    merged, mark = TariffSchedule([TariffPeriod(date(2026, 2, 1), _tariff())]) \
        .merge_newer(BUILTIN_SCHEDULE, None)
    assert len(merged) == 4
    assert mark == date(2026, 2, 1)
    # the stored 2026-02-01 wins over the built-in one
    assert merged.at(date(2026, 2, 1)).waste_fixed == Decimal("1")
