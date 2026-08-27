import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_READINGS, CONF_SOURCE, CONF_VAT_RATE, DOMAIN,
)

LEGACY_RESET_DAY = "reset_day"


def _v1_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={LEGACY_RESET_DAY: 13, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_migration_converts_store_state_to_a_reading(hass: HomeAssistant):
    hass.states.async_set("sensor.water_meter_total", "2640",
                          {"unit_of_measurement": "m³"})
    entry = _v1_entry(hass)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
    await store.async_save({"cycle_start": "2026-08-13", "baseline": "2631"})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert LEGACY_RESET_DAY not in entry.options
    # cycle_start - 1 day, so the derived cycle still starts on 2026-08-13
    assert entry.options[CONF_READINGS] == [
        {"date": "2026-08-12", "m3": "2631", "source": "auto"}
    ]
    assert await store.async_load() is None


async def test_migration_without_store_starts_empty(hass: HomeAssistant):
    hass.states.async_set("sensor.water_meter_total", "2640",
                          {"unit_of_measurement": "m³"})
    entry = _v1_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert LEGACY_RESET_DAY not in entry.options
    # no prior state to preserve -> the sensor platform seeds a reading itself
    assert len(entry.options[CONF_READINGS]) == 1
    assert entry.options[CONF_READINGS][0]["m3"] == "2640"


async def test_migration_preserves_the_cycle_boundary(hass: HomeAssistant):
    hass.states.async_set("sensor.water_meter_total", "2651",
                          {"unit_of_measurement": "m³"})
    entry = _v1_entry(hass)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
    await store.async_save({"cycle_start": "2026-08-13", "baseline": "2631"})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    attrs = hass.states.get("sensor.agere_total_cost").attributes
    assert attrs["cycle_start"] == "2026-08-13"
    assert attrs["cycle_consumption_m3"] == 20.0   # 2651 - 2631, baseline kept
