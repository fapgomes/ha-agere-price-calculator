import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TARIFFS, CONF_TARIFFS_SEEDED_THROUGH, CONF_TAXES,
    CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)

READINGS = [
    {"date": "2026-07-10", "m3": "512", "source": "manual"},
    {"date": "2026-08-12", "m3": "536", "source": "manual"},
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
    hass.states.async_set("sensor.water_meter_total", "543",
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={
            CONF_WATER: True, CONF_SANITATION: True, CONF_WASTE: True,
            CONF_TAXES: True, CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
            CONF_READINGS: READINGS, **options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_menu_lists_the_four_sections(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "readings", "next_reading", "tariffs", "components",
    }


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
    assert entry.options[CONF_READINGS] == READINGS   # untouched


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
        result["flow_id"], {"date": "2026-09-03", "m3": 543, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_READINGS][-1] == {
        "date": "2026-09-03", "m3": "543", "source": "manual"
    }


async def test_edit_a_past_reading_date(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-07-10")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-07-12", "m3": 512, "delete": False}
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
        result["flow_id"], {"date": "2026-07-10", "m3": 512, "delete": True}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [r["date"] for r in entry.options[CONF_READINGS]] == ["2026-08-12"]


async def test_invalid_edit_shows_the_error_in_the_form(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-08-12")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-08-12", "m3": 300, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_reading"
    assert entry.options[CONF_READINGS] == READINGS   # nothing written


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


async def test_invalid_edit_surfaces_the_concrete_reason(hass: HomeAssistant):
    """The form must say WHICH readings conflict — a generic 'invalid' message
    leaves the user guessing (e.g. after entering Consumo instead of Leitura)."""
    entry = await _entry(hass)
    result = await _open_reading(hass, entry, "2026-08-12")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"date": "2026-08-12", "m3": 300, "delete": False}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_reading"
    reason = result["description_placeholders"]["error"]
    assert "2026-08-12" in reason
    assert "2026-07-10" in reason
    assert "512" in reason


# --- tariffs ---

TARIFF_FORM = {
    "effective_from": "2027-01-01",
    "water_tier_bounds": "5,10,15,25",
    "water_tier_price_1": "0.52",
    "water_tier_price_2": "0.68",
    "water_tier_price_3": "0.88",
    "water_tier_price_4": "1.92",
    "water_tier_price_5": "2.75",
    "water_availability": "5.00",
    "sanitation_drainage": "0.49",
    "sanitation_availability": "5.00",
    "waste_variable": "0.015",
    "waste_fixed": "2.60",
    "tax_water": "0.0382",
    "tax_sanitation": "0.0150",
    "tax_waste_mgmt": "2.95",
    "delete": False,
}


async def _open_tariff(hass: HomeAssistant, entry: MockConfigEntry, value: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tariffs"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"tariff": value}
    )


async def test_add_a_tariff_copies_the_newest_forward(hass: HomeAssistant):
    """The form for a new entry arrives pre-filled from the newest snapshot, so
    only what changed has to be typed."""
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    suggested = {
        key.schema: key.description["suggested_value"]
        for key in result["data_schema"].schema
        if getattr(key, "description", None)
    }
    assert suggested["water_availability"] == "4.8623"
    assert suggested["water_tier_price_1"] == "0.508"
    assert suggested["water_tier_bounds"] == "5,10,15,25"


async def test_add_a_tariff(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], TARIFF_FORM
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    added = next(t for t in entry.options[CONF_TARIFFS]
                 if t["effective_from"] == "2027-01-01")
    assert added["water_availability"] == "5"
    assert added["water_tier_prices"] == ["0.52", "0.68", "0.88", "1.92", "2.75"]


async def test_edit_a_tariff(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "2026-02-01")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "effective_from": "2026-02-01",
                            "water_availability": "4.90"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    stored = {t["effective_from"]: t for t in entry.options[CONF_TARIFFS]}
    assert stored["2026-02-01"]["water_availability"] == "4.9"
    assert len(stored) == 4


async def test_delete_a_tariff(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "2026-01-01")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "effective_from": "2026-01-01",
                            "delete": True}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [t["effective_from"] for t in entry.options[CONF_TARIFFS]] == [
        "2024-12-12", "2025-01-01", "2026-02-01",
    ]


async def test_cannot_delete_the_last_tariff(hass: HomeAssistant):
    entry = await _entry(hass, **{CONF_TARIFFS: [{
        "effective_from": "2026-02-01",
        "water_tier_bounds": [5, 10, 15, 25],
        "water_tier_prices": ["0.508", "0.6636", "0.8605", "1.8765", "2.6852"],
        "water_availability": "4.8623", "sanitation_drainage": "0.4809",
        "sanitation_availability": "4.8766", "waste_variable": "0.0147",
        "waste_fixed": "2.5257", "tax_water": "0.0382",
        "tax_sanitation": "0.015", "tax_waste_mgmt": "2.8821",
    }], CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01"})
    result = await _open_tariff(hass, entry, "2026-02-01")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "effective_from": "2026-02-01",
                            "delete": True}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_tariff"
    assert "at least one" in result["description_placeholders"]["error"]


async def test_an_empty_top_tier_price_means_unknown(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "water_tier_price_5": ""}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    added = next(t for t in entry.options[CONF_TARIFFS]
                 if t["effective_from"] == "2027-01-01")
    assert added["water_tier_prices"][4] is None


async def test_invalid_tariff_value_shows_the_reason(hass: HomeAssistant):
    entry = await _entry(hass)
    before = entry.options[CONF_TARIFFS]
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "waste_fixed": "abc"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_tariff"
    assert "waste_fixed" in result["description_placeholders"]["error"]
    assert entry.options[CONF_TARIFFS] == before


async def test_invalid_tier_bounds_show_the_reason(hass: HomeAssistant):
    entry = await _entry(hass)
    result = await _open_tariff(hass, entry, "new")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TARIFF_FORM, "water_tier_bounds": "5,5,15,25"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_tariff"
    assert "increasing" in result["description_placeholders"]["error"]
