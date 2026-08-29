from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from custom_components.agere_water.calculator import (
    calcular, marginal_price, money, price4, tier_limits,
)
from custom_components.agere_water.const import CalcConfig
from custom_components.agere_water.tariffs import (
    BUILTIN_SCHEDULE, TariffPeriod, TariffSchedule, UnknownTariffValue,
)

CFG = CalcConfig()


def test_calc_config_defaults():
    assert CFG.include_vat is True
    assert CFG.vat_rate == Decimal("0.06")
    assert CFG.schedule is BUILTIN_SCHEDULE


def test_money_rounds_half_up():
    assert money(Decimal("3.7014")) == Decimal("3.70")
    assert money(Decimal("13.4652")) == Decimal("13.47")
    assert money(Decimal("2.6544")) == Decimal("2.65")


def test_price4_rounds_half_up():
    assert price4(Decimal("1.23455")) == Decimal("1.2346")
    assert price4(Decimal("0.50805")) == Decimal("0.5081")


def test_tier_limits_prorate_half_up():
    assert tier_limits(30, (5, 10, 15, 25)) == [5, 10, 15, 25]
    assert tier_limits(28, (5, 10, 15, 25)) == [5, 9, 14, 23]
    assert tier_limits(33, (5, 10, 15, 25)) == [6, 11, 17, 28]
    assert tier_limits(12, (5, 10, 15, 25)) == [2, 4, 6, 10]
    assert tier_limits(15, (5, 10, 15, 25)) == [3, 5, 8, 13]


def test_tier_limits_at_32_days_keep_round_not_ceil():
    """A real 32-day invoice used tier limit 6, i.e. ceil(5*32/30) = 6. Three
    independent 28-day invoices need round (10*28/30 = 9.333 -> 9), and ceil
    would give 10 there. No single rule fits both, so round stays and the
    32-day case is off by about 0.16 EUR. Do not "fix" this without a 31- or
    32-day invoice that settles it."""
    assert tier_limits(32, (5, 10, 15, 25)) == [5, 11, 16, 27]


# --- continuity: a period crossing no change must match the old engine ---

def test_published_invoice_28m3_30days():
    bd = calcular(date(2026, 5, 14), date(2026, 6, 12), Decimal("28"), CFG)
    assert bd.total == Decimal("71.21")


def test_published_invoice_18m3_28days():
    bd = calcular(date(2026, 6, 13), date(2026, 7, 10), Decimal("18"), CFG)
    assert bd.total == Decimal("44.21")


def test_period_without_a_change_has_one_line_per_component():
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), CFG)
    assert bd.water == Decimal("24.40")
    assert bd.sanitation == Decimal("14.50")
    assert bd.waste == Decimal("2.82")
    assert bd.taxes == Decimal("3.94")
    assert bd.total == Decimal("48.06")
    assert [l.component for l in bd.lines if l.component.startswith("water_tier")] == [
        "water_tier_1", "water_tier_2", "water_tier_3", "water_tier_4",
    ]
    assert all(l.start is None for l in bd.lines if l.component in (
        "water_availability", "sanitation_availability", "waste_fixed",
        "tax_waste_mgmt",
    ))


# --- the split, per component ---

def test_split_at_2026_02_01_restarts_the_water_tiers():
    """A: 7 m3 in 12 days, limits 2/4/6/10 -> 2+2+2+1 at the old prices.
    B: 8 m3 in 15 days, limits 3/5/8/13 -> 3+2+3 at the new prices, back to
    tier 1."""
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    water = [l for l in bd.lines if l.component.startswith("water_tier")]
    assert [(l.component, str(l.qty), str(l.rate), str(l.value)) for l in water] == [
        ("water_tier_1", "2", "0.4751", "0.95"),
        ("water_tier_2", "2", "0.6206", "1.24"),
        ("water_tier_3", "2", "0.8048", "1.61"),
        ("water_tier_4", "1", "1.7550", "1.76"),
        ("water_tier_1", "3", "0.5080", "1.52"),
        ("water_tier_2", "2", "0.6636", "1.33"),
        ("water_tier_3", "3", "0.8605", "2.58"),
    ]
    assert water[0].start == date(2026, 1, 20)
    assert water[0].end == date(2026, 1, 31)
    assert water[4].start == date(2026, 2, 1)
    assert water[4].end == date(2026, 2, 15)


def test_split_bills_the_fixed_charges_once_at_the_end_tariff():
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    fixed = {l.component: l for l in bd.lines if l.start is None}
    assert len(fixed) == 4
    assert fixed["water_availability"].rate == Decimal("4.8623")
    assert fixed["sanitation_availability"].rate == Decimal("4.8766")
    assert fixed["waste_fixed"].rate == Decimal("2.5257")
    assert fixed["tax_waste_mgmt"].rate == Decimal("2.8821")


def test_split_at_2026_02_01_totals():
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    assert bd.water == Decimal("15.85")
    assert bd.vat == Decimal("1.71")
    assert bd.total == Decimal("35.80")


def test_split_at_2025_01_01_touches_only_the_taxes():
    """This is the mechanism that a period-wide split would get wrong: on
    2025-01-01 only the resource taxes moved, so water stays a single line over
    all 27 days and the taxes split 7 + 8 m3."""
    bd = calcular(date(2024, 12, 20), date(2025, 1, 15), Decimal("15"), CFG)
    water = [l for l in bd.lines if l.component.startswith("water_tier")]
    assert len(water) == 4
    assert all(l.start == date(2024, 12, 20) for l in water)
    assert [str(l.qty) for l in water] == ["5", "4", "5", "1"]

    tax_water = [l for l in bd.lines if l.component == "tax_water"]
    assert [(str(l.qty), str(l.rate), str(l.value)) for l in tax_water] == [
        ("7", "0.0379", "0.27"),
        ("8", "0.0382", "0.31"),
    ]
    drainage = [l for l in bd.lines if l.component == "sanitation_drainage"]
    assert len(drainage) == 1
    assert bd.total == Decimal("33.63")


# --- structure and VAT ---

def test_subtotals_are_sums_of_their_lines():
    bd = calcular(date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), CFG)
    for attr, prefix in (
        ("water", "water"), ("sanitation", "sanitation"),
        ("waste", "waste"), ("taxes", "tax"),
    ):
        assert getattr(bd, attr) == sum(
            (l.value for l in bd.lines if l.component.startswith(prefix)),
            Decimal(0),
        )
    assert bd.base_without_vat == sum((l.value for l in bd.lines), Decimal(0))
    assert bd.total == bd.base_without_vat + bd.vat


def test_vat_flag_encodes_the_civa_exemption():
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), CFG)
    exempt = {l.component for l in bd.lines if not l.vat}
    assert exempt == {"waste_variable", "waste_fixed", "tax_waste_mgmt"}
    assert bd.vat == money(
        sum((l.value for l in bd.lines if l.vat), Decimal(0)) * Decimal("0.06")
    )


def test_disabled_components_produce_no_lines():
    cfg = CalcConfig(include_sanitation=False, include_waste=False)
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), cfg)
    assert bd.sanitation == Decimal(0)
    assert bd.waste == Decimal(0)
    assert not [l for l in bd.lines if l.component.startswith(("sanitation", "waste"))]
    assert bd.water == Decimal("24.40")


def test_vat_can_be_switched_off():
    cfg = CalcConfig(include_vat=False)
    bd = calcular(date(2026, 3, 1), date(2026, 3, 30), Decimal("20"), cfg)
    assert bd.vat == Decimal(0)
    assert bd.total == bd.base_without_vat


# --- refusals ---

def test_unknown_tier_price_raises_and_names_the_tier():
    """A tariff may carry an unknown price — a user adding an older one can
    leave it empty. Reaching that tier must refuse rather than undercharge."""
    gappy = TariffSchedule([TariffPeriod(
        date(2025, 1, 1),
        replace(
            BUILTIN_SCHEDULE.at(date(2025, 6, 1)),
            water_tier_prices=BUILTIN_SCHEDULE.at(
                date(2025, 6, 1)
            ).water_tier_prices[:4] + (None,),
        ),
    )])
    with pytest.raises(UnknownTariffValue, match="tier 5"):
        calcular(date(2025, 3, 1), date(2025, 3, 30), Decimal("30"),
                 CalcConfig(schedule=gappy))


def test_a_period_starting_before_the_earliest_tariff_raises():
    with pytest.raises(ValueError, match="2024-12-12"):
        calcular(date(2023, 1, 1), date(2023, 1, 30), Decimal("10"), CFG)


def test_end_before_start_raises():
    with pytest.raises(ValueError, match="after"):
        calcular(date(2026, 3, 30), date(2026, 3, 1), Decimal("10"), CFG)


# --- marginal price ---

def test_marginal_price_uses_the_tariff_in_force_today():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    assert marginal_price(
        date(2026, 3, 1), date(2026, 3, 30), Decimal("12"), date(2026, 3, 20), cfg
    ) == Decimal("0.8605")


def test_marginal_price_in_a_split_period_uses_todays_sub_period():
    """Today sits in the second sub-period: 15 days, 8 m3 of the 15 allocated to
    it, limits 3/5/8/13. The next m3 is therefore in tier 4 at the NEW price,
    not tier 3 at the old one — the sub-period's own tariff and day count decide."""
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    assert marginal_price(
        date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), date(2026, 2, 10), cfg
    ) == Decimal("1.8765")


def test_marginal_price_in_the_first_sub_period_uses_the_old_price():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    # 7 m3 allocated to 20/01-31/01, 12 days, limits 2/4/6/10 -> tier 4, old price
    assert marginal_price(
        date(2026, 1, 20), date(2026, 2, 15), Decimal("15"), date(2026, 1, 25), cfg
    ) == Decimal("1.7550")


def test_marginal_price_full_with_vat():
    cfg = CalcConfig()
    # tier 1 0.5080 + drainage 0.4809 + taxes 0.0382 + 0.0150 = 1.0421
    # x 1.06 = 1.104626 ; + waste variable 0.0147 (no VAT) -> 1.1193
    assert marginal_price(
        date(2026, 3, 1), date(2026, 3, 30), Decimal("0"), date(2026, 3, 1), cfg
    ) == Decimal("1.1193")
