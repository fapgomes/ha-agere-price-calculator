from decimal import Decimal

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)

READINGS = [
    {"date": "2026-06-12", "m3": "500", "source": "manual"},
    {"date": "2026-07-10", "m3": "512", "source": "manual"},
    {"date": "2026-08-12", "m3": "536", "source": "manual"},
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
    await _setup(hass, "536", **{CONF_READINGS: READINGS})
    for entity_id in (
        "sensor.agere_total_cost", "sensor.agere_marginal_price",
        "sensor.agere_cycle_consumption", "sensor.agere_water_cost",
        "sensor.agere_last_invoice",
    ):
        assert hass.states.get(entity_id) is not None


async def test_last_invoice_exposes_every_closed_cycle(hass: HomeAssistant):
    await _setup(hass, "536", **{CONF_READINGS: READINGS})
    state = hass.states.get("sensor.agere_last_invoice")
    assert Decimal(state.state) == Decimal("55.82")
    cycles = state.attributes["cycles"]
    assert cycles[0] == {
        "start": "2026-06-13", "end": "2026-07-10",
        "days": 28, "m3": 12.0, "total": 30.95,
    }
    assert cycles[1] == {
        "start": "2026-07-11", "end": "2026-08-12",
        "days": 33, "m3": 24.0, "total": 55.82,
    }


async def test_current_cycle_uses_next_reading_date(hass: HomeAssistant):
    await _setup(hass, "543", **{
        CONF_READINGS: READINGS,
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
    await _setup(hass, "543", **{CONF_READINGS: READINGS})
    attrs = hass.states.get("sensor.agere_total_cost").attributes
    assert attrs["billing_days"] == 33          # learned from the previous cycle
    assert attrs["billing_days_estimated"] is True
    assert attrs["next_reading_date"] is None


async def test_total_cost_recomputes_on_source_change(hass: HomeAssistant):
    await _setup(hass, "536", **{
        CONF_READINGS: READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    before = Decimal(hass.states.get("sensor.agere_total_cost").state)
    hass.states.async_set("sensor.water_meter_total", "541",
                          {"unit_of_measurement": "m³"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.agere_cycle_consumption").state == "5.0"
    assert Decimal(hass.states.get("sensor.agere_total_cost").state) > before


async def test_empty_log_seeds_an_auto_reading(hass: HomeAssistant):
    entry = await _setup(hass, "536")
    await hass.async_block_till_done()
    stored = entry.options[CONF_READINGS]
    assert len(stored) == 1
    assert stored[0]["m3"] == "536"
    assert stored[0]["source"] == "auto"


async def test_seeding_does_not_repeat(hass: HomeAssistant):
    entry = await _setup(hass, "536")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.water_meter_total", "540",
                          {"unit_of_measurement": "m³"})
    await hass.async_block_till_done()
    assert len(entry.options[CONF_READINGS]) == 1


async def test_malformed_readings_do_not_break_setup(hass: HomeAssistant):
    await _setup(hass, "536", **{
        CONF_READINGS: [{"date": "not-a-date", "m3": "536"}],
    })
    assert hass.states.get("sensor.agere_total_cost") is not None


async def test_monetary_sensors_use_total_with_last_reset(hass: HomeAssistant):
    """device_class monetary forbids total_increasing; the per-period reset is
    carried by last_reset instead. An invalid pair leaves the sensor without
    long-term statistics, which is what the Energy dashboard reads."""
    await _setup(hass, "543", **{
        CONF_READINGS: READINGS,
        CONF_NEXT_READING_DATE: "2026-09-03",
    })
    for entity_id in (
        "sensor.agere_total_cost", "sensor.agere_water_cost",
        "sensor.agere_sanitation_cost", "sensor.agere_waste_cost",
        "sensor.agere_taxes_cost",
    ):
        attrs = hass.states.get(entity_id).attributes
        assert attrs["device_class"] == "monetary", entity_id
        assert attrs["state_class"] == "total", entity_id
        assert attrs["last_reset"].startswith("2026-08-13"), entity_id


async def test_consumption_sensor_keeps_total_increasing(hass: HomeAssistant):
    await _setup(hass, "543", **{CONF_READINGS: READINGS})
    attrs = hass.states.get("sensor.agere_cycle_consumption").attributes
    assert attrs["device_class"] == "water"
    assert attrs["state_class"] == "total_increasing"   # valid for water
