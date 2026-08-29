"""Constants and tariff configuration for the AGERE Water Price integration."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .tariffs import BUILTIN_SCHEDULE, TariffSchedule

DOMAIN = "agere_water"
PLATFORMS = ["sensor"]

# --- config entry keys ---
CONF_SOURCE = "source_entity"
CONF_INCLUDE_VAT = "include_vat"
CONF_VAT_RATE = "vat_rate"
CONF_WATER = "enable_water"
CONF_SANITATION = "enable_sanitation"
CONF_WASTE = "enable_waste"
CONF_TAXES = "enable_taxes"
CONF_READINGS = "readings"
CONF_NEXT_READING_DATE = "next_reading_date"
CONF_TARIFFS = "tariffs"
CONF_TARIFFS_SEEDED_THROUGH = "tariffs_seeded_through"

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


@dataclass(frozen=True)
class CalcConfig:
    """Everything the calculator needs beyond the period and its consumption."""

    schedule: TariffSchedule = BUILTIN_SCHEDULE
    include_water: bool = True
    include_sanitation: bool = True
    include_waste: bool = True
    include_taxes: bool = True
    include_vat: bool = True
    vat_rate: Decimal = DEFAULT_VAT_RATE

