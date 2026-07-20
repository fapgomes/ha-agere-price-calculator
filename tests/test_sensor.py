from decimal import Decimal

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_RESET_DAY, CONF_SANITATION, CONF_SOURCE,
    CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)


async def _setup(hass: HomeAssistant, meter_state: str) -> None:
    hass.states.async_set("sensor.water_meter_total", meter_state,
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_RESET_DAY: 13, CONF_WATER: True, CONF_SANITATION: True,
            CONF_WASTE: True, CONF_TAXES: True, CONF_INCLUDE_VAT: True,
            CONF_VAT_RATE: "0.06",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensors_created(hass: HomeAssistant):
    await _setup(hass, "2620")
    assert hass.states.get("sensor.agere_total_cost") is not None
    assert hass.states.get("sensor.agere_marginal_price") is not None
    assert hass.states.get("sensor.agere_cycle_consumption") is not None
    assert hass.states.get("sensor.agere_water_cost") is not None


async def test_total_cost_recomputes_on_source_change(hass: HomeAssistant):
    await _setup(hass, "2620")            # baseline captured at 2620
    hass.states.async_set("sensor.water_meter_total", "2648",
                          {"unit_of_measurement": "m³"})  # +28 m³
    await hass.async_block_till_done()
    cost = hass.states.get("sensor.agere_total_cost")
    # 28 m³ this cycle; days depend on test clock, but cost must be > fixed charges (~15€)
    assert Decimal(cost.state) > Decimal("15")
    assert hass.states.get("sensor.agere_cycle_consumption").state == "28"
