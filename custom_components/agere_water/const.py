"""Constants and tariff configuration for the AGERE Water Price integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

DOMAIN = "agere_water"
PLATFORMS = ["sensor"]

# --- config entry keys ---
CONF_SOURCE = "source_entity"
CONF_RESET_DAY = "reset_day"
CONF_INCLUDE_VAT = "include_vat"
CONF_VAT_RATE = "vat_rate"
CONF_WATER = "enable_water"
CONF_SANITATION = "enable_sanitation"
CONF_WASTE = "enable_waste"
CONF_TAXES = "enable_taxes"

# tariff override keys (stored as strings in options, parsed to Decimal)
CONF_WATER_TIER_PRICES = "water_tier_prices"      # list[str], length 5
CONF_WATER_TIER_BOUNDS = "water_tier_bounds"      # list[int], length 4
CONF_WATER_AVAILABILITY = "water_availability"
CONF_SANITATION_DRAINAGE = "sanitation_drainage"
CONF_SANITATION_AVAILABILITY = "sanitation_availability"
CONF_WASTE_VARIABLE = "waste_variable"
CONF_WASTE_FIXED = "waste_fixed"
CONF_TAX_WATER = "tax_water"
CONF_TAX_SANITATION = "tax_sanitation"
CONF_TAX_WASTE_MGMT = "tax_waste_mgmt"

DEFAULT_VAT_RATE = Decimal("0.06")
DEFAULT_RESET_DAY = 13


@dataclass(frozen=True)
class Tariff:
    """AGERE Doméstico tariff values (2026 defaults)."""

    water_tier_bounds: tuple[int, ...] = (5, 10, 15, 25)
    water_tier_prices: tuple[Decimal, ...] = (
        Decimal("0.5080"),
        Decimal("0.6636"),
        Decimal("0.8605"),
        Decimal("1.8765"),
        Decimal("2.6852"),
    )
    water_availability: Decimal = Decimal("4.8623")
    sanitation_drainage: Decimal = Decimal("0.4809")
    sanitation_availability: Decimal = Decimal("4.8766")
    waste_variable: Decimal = Decimal("0.0147")
    waste_fixed: Decimal = Decimal("2.5257")
    tax_water: Decimal = Decimal("0.0382")
    tax_sanitation: Decimal = Decimal("0.0150")
    tax_waste_mgmt: Decimal = Decimal("2.8821")


@dataclass(frozen=True)
class CalcConfig:
    """Everything the calculator needs beyond consumption and days."""

    tariff: Tariff = field(default_factory=Tariff)
    include_water: bool = True
    include_sanitation: bool = True
    include_waste: bool = True
    include_taxes: bool = True
    include_vat: bool = True
    vat_rate: Decimal = DEFAULT_VAT_RATE


DEFAULT_TARIFF = Tariff()
