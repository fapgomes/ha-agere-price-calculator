from decimal import Decimal

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
