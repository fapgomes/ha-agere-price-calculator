import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)

INVOICE_READINGS = [
    {"date": "2026-07-10", "m3": "2611", "source": "manual"},
    {"date": "2026-08-12", "m3": "2631", "source": "manual"},
]


async def test_user_flow_creates_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE: "sensor.water_meter_total"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE] == "sensor.water_meter_total"
    assert result["options"][CONF_READINGS] == []
    assert "reset_day" not in result["options"]


async def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    hass.states.async_set("sensor.water_meter_total", "2638",
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_WATER: True, CONF_SANITATION: True, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
            CONF_READINGS: INVOICE_READINGS, **options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_menu_lists_the_three_sections(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert set(result["menu_options"]) == {"readings", "next_reading", "components"}


async def test_components_step_keeps_the_readings(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "components"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_WATER: True, CONF_SANITATION: False, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SANITATION] is False
    assert entry.options[CONF_READINGS] == INVOICE_READINGS   # untouched


async def _open_reading(hass: HomeAssistant, entry: MockConfigEntry, value: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "readings"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"reading": value}
    )


async def test_add_a_new_reading(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-09-03", "m3": 2638, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_READINGS][-1] == {
        "date": "2026-09-03", "m3": "2638", "source": "manual"
    }


async def test_edit_a_past_reading_date(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-07-10")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-07-12", "m3": 2611, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [r["date"] for r in entry.options[CONF_READINGS]] == [
        "2026-07-12", "2026-08-12"
    ]


async def test_delete_a_reading(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-07-10")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-07-10", "m3": 2611, "delete": True}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [r["date"] for r in entry.options[CONF_READINGS]] == ["2026-08-12"]


async def test_invalid_edit_shows_the_error_in_the_form(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-08-12")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-08-12", "m3": 2000, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_reading"
    assert entry.options[CONF_READINGS] == INVOICE_READINGS   # nothing written


async def test_set_and_clear_next_reading_date(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "next_reading"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-09-03", "clear": False}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] == "2026-09-03"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "next_reading"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-09-03", "clear": True}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_NEXT_READING_DATE] is None
