from decimal import Decimal

from custom_components.agere_water.calculator import money, price4, tier_limits, water_lines
from custom_components.agere_water.const import DEFAULT_TARIFF, CalcConfig


def test_default_tariff_values():
    assert DEFAULT_TARIFF.water_tier_bounds == (5, 10, 15, 25)
    assert DEFAULT_TARIFF.water_tier_prices[0] == Decimal("0.5080")
    assert DEFAULT_TARIFF.water_availability == Decimal("4.8623")


def test_calc_config_defaults():
    cfg = CalcConfig()
    assert cfg.include_vat is True
    assert cfg.vat_rate == Decimal("0.06")
    assert cfg.tariff.tax_waste_mgmt == Decimal("2.8821")


def test_money_rounds_half_up():
    assert money(Decimal("3.7014")) == Decimal("3.70")
    assert money(Decimal("13.4652")) == Decimal("13.47")
    assert money(Decimal("2.6544")) == Decimal("2.65")


def test_tier_limits_30_days_unchanged():
    assert tier_limits(30, (5, 10, 15, 25)) == [5, 10, 15, 25]


def test_tier_limits_28_days_prorated():
    assert tier_limits(28, (5, 10, 15, 25)) == [5, 9, 14, 23]


def test_price4_rounds_half_up():
    assert price4(Decimal("1.23455")) == Decimal("1.2346")
    assert price4(Decimal("0.50805")) == Decimal("0.5081")


def test_water_lines_28m3_30days():
    lines = water_lines(Decimal("28"), 30, DEFAULT_TARIFF)
    assert [l.qty for l in lines] == [Decimal(x) for x in (5, 5, 5, 10, 3)]
    assert [l.value for l in lines] == [
        Decimal("2.54"), Decimal("3.32"), Decimal("4.30"),
        Decimal("18.77"), Decimal("8.06"),
    ]
    assert sum(l.value for l in lines) == Decimal("36.99")


def test_water_lines_18m3_28days():
    lines = water_lines(Decimal("18"), 28, DEFAULT_TARIFF)
    assert [l.qty for l in lines] == [Decimal(x) for x in (5, 4, 5, 4, 0)]
    assert [l.value for l in lines] == [
        Decimal("2.54"), Decimal("2.65"), Decimal("4.30"),
        Decimal("7.51"), Decimal("0.00"),
    ]
    assert sum(l.value for l in lines) == Decimal("17.00")
