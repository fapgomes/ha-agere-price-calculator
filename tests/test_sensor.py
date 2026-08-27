from decimal import Decimal

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)

INVOICE_READINGS = [
    {"date": "2026-06-12", "m3": "2593", "source": "manual"},
    {"date": "2026-07-10", "m3": "2611", "source": "manual"},
    {"date": "2026-08-12", "m3": "2631", "source": "manual"},
]


async def _setup(hass: HomeAssistant, meter_state: str, **extra_options):
    hass.states.async_set("sensor.water_meter_total", meter_state,
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_WATER: True, CONF_SANITATION: True, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
            **extra_options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_created(hass: HomeAssistant):
    await _setup(hass, "2631", **{CONF_READINGS: INVOICE_READINGS})
    for entity_id in (
        "sensor.agere_total_cost", "sensor.agere_marginal_price",
        "sensor.agere_cycle_consumption", "sensor.agere_water_cost",
        "sensor.agere_last_invoice",
    ):
        assert hass.states.get(entity_id) is not None


async def test_last_invoice_reproduces_both_closed_cycles(hass: HomeAssistant):
    await _setup(hass, "2631", **{CONF_READINGS: INVOICE_READINGS})
    state = hass.states.get("sensor.agere_last_invoice")
    assert Decimal(state.state) == Decimal("45.53")
    cycles = state.attributes["cycles"]
    assert cycles[0] == {
        "start": "2026-06-13", "end": "2026-07-10",
        "days": 28, "m3": 18.0, "total": 44.21,
    }
    assert cycles[1] == {
        "start": "2026-07-11", "end": "2026-08-12",
        "days": 33, "m3": 20.0, "total": 45.53,
    }


async def test_current_cycle_uses_next_reading_date(hass: HomeAssistant):
    await _setup(hass, "2638", **{
        CONF_READINGS: INVOICE_READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    attrs = hass.states.get("sensor.agere_total_cost").attributes
    assert attrs["cycle_start"] == "2026-08-13"
    assert attrs["cycle_end"] == "2026-09-03"
    assert attrs["billing_days"] == 22
    assert attrs["billing_days_estimated"] is False
    assert attrs["next_reading_date"] == "2026-09-03"
    assert attrs["cycle_consumption_m3"] == 7.0


async def test_current_cycle_estimated_without_next_reading_date(hass: HomeAssistant):
    await _setup(hass, "2638", **{CONF_READINGS: INVOICE_READINGS})
    attrs = hass.states.get("sensor.agere_total_cost").attributes
    assert attrs["billing_days"] == 33          # learned from the previous cycle
    assert attrs["billing_days_estimated"] is True
    assert attrs["next_reading_date"] is None


async def test_total_cost_recomputes_on_source_change(hass: HomeAssistant):
    await _setup(hass, "2631", **{
        CONF_READINGS: INVOICE_READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    before = Decimal(hass.states.get("sensor.agere_total_cost").state)
    hass.states.async_set("sensor.water_meter_total", "2636",
                          {"unit_of_measurement": "m³"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.agere_cycle_consumption").state == "5.0"
    assert Decimal(hass.states.get("sensor.agere_total_cost").state) > before


async def test_empty_log_seeds_an_auto_reading(hass: HomeAssistant):
    entry = await _setup(hass, "2631")
    await hass.async_block_till_done()
    stored = entry.options[CONF_READINGS]
    assert len(stored) == 1
    assert stored[0]["m3"] == "2631"
    assert stored[0]["source"] == "auto"


async def test_seeding_does_not_repeat(hass: HomeAssistant):
    entry = await _setup(hass, "2631")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.water_meter_total", "2635",
                          {"unit_of_measurement": "m³"})
    await hass.async_block_till_done()
    assert len(entry.options[CONF_READINGS]) == 1


async def test_malformed_readings_do_not_break_setup(hass: HomeAssistant):
    await _setup(hass, "2631", **{
        CONF_READINGS: [{"date": "not-a-date", "m3": "2631"}],
    })
    assert hass.states.get("sensor.agere_total_cost") is not None
