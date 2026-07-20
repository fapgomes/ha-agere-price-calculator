from decimal import Decimal

from custom_components.agere_water.calculator import calcular, money, price4, tier_limits, water_lines, marginal_price
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


def test_full_bill_28m3_30days():
    bd = calcular(Decimal("28"), 30, CalcConfig())
    assert bd.water == Decimal("41.85")
    assert bd.sanitation == Decimal("18.35")
    assert bd.waste == Decimal("2.94")
    assert bd.taxes == Decimal("4.37")
    assert bd.base_without_vat == Decimal("67.51")
    assert bd.vat == Decimal("3.70")
    assert bd.total == Decimal("71.21")


def test_full_bill_18m3_28days():
    bd = calcular(Decimal("18"), 28, CalcConfig())
    assert bd.water == Decimal("21.86")
    assert bd.sanitation == Decimal("13.54")
    assert bd.waste == Decimal("2.79")
    assert bd.taxes == Decimal("3.84")
    assert bd.base_without_vat == Decimal("42.03")
    assert bd.vat == Decimal("2.18")
    assert bd.total == Decimal("44.21")


def test_vat_excluded():
    bd = calcular(Decimal("28"), 30, CalcConfig(include_vat=False))
    assert bd.vat == Decimal("0.00")
    assert bd.total == Decimal("67.51")


def test_water_only_component():
    bd = calcular(
        Decimal("28"), 30,
        CalcConfig(include_sanitation=False, include_waste=False, include_taxes=False),
    )
    assert bd.sanitation == Decimal("0")
    assert bd.water == Decimal("41.85")
    # VAT only on water: 41.85 * 0.06 = 2.511 -> 2.51
    assert bd.vat == Decimal("2.51")
    assert bd.total == Decimal("44.36")


def test_waste_never_taxed():
    # Waste-only, VAT on: waste is not subject to VAT, so vat must be 0.
    bd = calcular(
        Decimal("28"), 30,
        CalcConfig(include_water=False, include_sanitation=False, include_taxes=False),
    )
    assert bd.waste == Decimal("2.94")
    assert bd.vat == Decimal("0.00")
    assert bd.total == Decimal("2.94")


def test_marginal_price_first_tier_no_vat_water_only():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    # 0 m³ consumed -> next m³ in tier 0 -> 0.5080
    assert marginal_price(Decimal("0"), 30, cfg) == Decimal("0.5080")


def test_marginal_price_selects_tier_by_consumption():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    # 12 m³ (30-day limits 5/10/15/25) -> next m³ in tier [10-15] -> 0.8605
    assert marginal_price(Decimal("12"), 30, cfg) == Decimal("0.8605")


def test_marginal_price_top_tier():
    cfg = CalcConfig(include_sanitation=False, include_waste=False,
                     include_taxes=False, include_vat=False)
    assert marginal_price(Decimal("40"), 30, cfg) == Decimal("2.6852")


def test_marginal_price_full_with_vat():
    cfg = CalcConfig()  # all components + 6% VAT
    # tier 0 water 0.5080 + drainage 0.4809 + tax_water 0.0382 + tax_sanit 0.0150 = 1.0421
    # subject *1.06 = 1.104626 ; + waste_variable 0.0147 (no VAT) = 1.119326 -> 1.1193
    assert marginal_price(Decimal("0"), 30, cfg) == Decimal("1.1193")
