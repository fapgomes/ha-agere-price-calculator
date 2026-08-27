import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)


async def _entry(hass: HomeAssistant, readings=None) -> MockConfigEntry:
    hass.states.async_set("sensor.water_meter_total", "2631",
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_WATER: True, CONF_SANITATION: True, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
            CONF_READINGS: readings if readings is not None else [],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_set_reading_inserts_and_returns_the_cycle(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-07-10", "m3": "2611", "source": "manual"},
    ])
    response = await hass.services.async_call(
        DOMAIN, "set_reading",
        {"date": "2026-08-12", "m3": 2631},
        blocking=True, return_response=True,
    )
    await hass.async_block_till_done()
    assert response["cycle"] == {
        "start": "2026-07-11", "end": "2026-08-12", "days": 33,
        "consumption_m3": 20.0, "total": 45.53, "water": 22.02,
        "sanitation": 14.5, "waste": 2.82, "taxes": 3.94, "vat": 2.25,
    }
    assert entry.options[CONF_READINGS][-1]["m3"] == "2631"


async def test_set_reading_upserts_same_date(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-08-12", "m3": "2631", "source": "manual"},
    ])
    await hass.services.async_call(
        DOMAIN, "set_reading", {"date": "2026-08-12", "m3": 2632}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(entry.options[CONF_READINGS]) == 1
    assert entry.options[CONF_READINGS][0]["m3"] == "2632"


async def test_set_reading_rejects_decreasing_value(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-07-10", "m3": "2611", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="lower than"):
        await hass.services.async_call(
            DOMAIN, "set_reading", {"date": "2026-08-12", "m3": 2600}, blocking=True
        )


async def test_set_reading_without_m3_and_without_recorder_data(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-07-10", "m3": "2611", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="no meter value"):
        await hass.services.async_call(
            DOMAIN, "set_reading", {"date": "2026-08-12"}, blocking=True
        )


async def test_remove_reading(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-07-10", "m3": "2611", "source": "manual"},
        {"date": "2026-08-12", "m3": "2631", "source": "manual"},
    ])
    await hass.services.async_call(
        DOMAIN, "remove_reading", {"date": "2026-07-10"}, blocking=True
    )
    await hass.async_block_till_done()
    assert [r["date"] for r in entry.options[CONF_READINGS]] == ["2026-08-12"]


async def test_remove_missing_reading_raises(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-08-12", "m3": "2631", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="no reading"):
        await hass.services.async_call(
            DOMAIN, "remove_reading", {"date": "2026-01-01"}, blocking=True
        )


async def test_set_next_reading_date_and_clear(hass: HomeAssistant):
    entry = await _entry(hass, [
        {"date": "2026-08-12", "m3": "2631", "source": "manual"},
    ])
    await hass.services.async_call(
        DOMAIN, "set_next_reading_date", {"date": "2026-09-03"}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] == "2026-09-03"

    await hass.services.async_call(DOMAIN, "set_next_reading_date", {}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] is None


async def test_set_next_reading_date_before_last_reading_rejected(hass: HomeAssistant):
    await _entry(hass, [{"date": "2026-08-12", "m3": "2631", "source": "manual"}])
    with pytest.raises(ServiceValidationError, match="must be after"):
        await hass.services.async_call(
            DOMAIN, "set_next_reading_date", {"date": "2026-08-01"}, blocking=True
        )


async def test_service_requires_config_entry_when_ambiguous(hass: HomeAssistant):
    await _entry(hass)
    second = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={CONF_READINGS: []},
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="config_entry"):
        await hass.services.async_call(
            DOMAIN, "set_reading", {"date": "2026-08-12", "m3": 2631}, blocking=True
        )
