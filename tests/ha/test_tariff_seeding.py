import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.agere_water.const import (
    CONF_READINGS, CONF_SOURCE, CONF_TARIFFS, CONF_TARIFFS_SEEDED_THROUGH,
    CONF_VAT_RATE, DOMAIN,
)

READINGS = [{"date": "2026-08-12", "m3": "536", "source": "manual"}]

BASE_TARIFF = {
    "effective_from": "2024-12-12",
    "water_tier_bounds": [5, 10, 15, 25],
    "water_tier_prices": ["0.4751", "0.6206", "0.8048", "1.755", None],
    "water_availability": "4.5476", "sanitation_drainage": "0.4402",
    "sanitation_availability": "4.4635", "waste_variable": "0.0136",
    "waste_fixed": "2.331", "tax_water": "0.0379",
    "tax_sanitation": "0.0141", "tax_waste_mgmt": "2.426",
}


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    hass.states.async_set("sensor.water_meter_total", "543",
                          {"unit_of_measurement": "m³"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_SOURCE: "sensor.water_meter_total"},
        options={CONF_READINGS: READINGS, CONF_VAT_RATE: "0.06", **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_seeds_the_builtin_schedule(hass: HomeAssistant):
    entry = await _setup(hass)
    stored = entry.options[CONF_TARIFFS]
    assert [t["effective_from"] for t in stored] == [
        "2024-12-12", "2025-01-01", "2026-01-01", "2026-02-01",
    ]
    assert entry.options[CONF_TARIFFS_SEEDED_THROUGH] == "2026-02-01"


async def test_setup_does_not_rewrite_on_every_start(hass: HomeAssistant):
    """Writing options reloads the entry, so an unconditional write would loop."""
    entry = await _setup(hass)
    before = entry.options[CONF_TARIFFS]
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options[CONF_TARIFFS] == before


async def test_setup_keeps_an_edited_snapshot(hass: HomeAssistant):
    edited = [{
        "effective_from": "2026-02-01",
        "water_tier_bounds": [5, 10, 15, 25],
        "water_tier_prices": ["0.6", "0.7", "0.9", "1.9", "2.7"],
        "water_availability": "9.99", "sanitation_drainage": "0.5",
        "sanitation_availability": "5", "waste_variable": "0.02",
        "waste_fixed": "3", "tax_water": "0.04", "tax_sanitation": "0.02",
        "tax_waste_mgmt": "3",
    }]
    entry = await _setup(hass, **{
        CONF_TARIFFS: edited,
        CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01",
    })
    stored = entry.options[CONF_TARIFFS]
    assert len(stored) == 1
    assert stored[0]["water_availability"] == "9.99"


async def test_setup_does_not_resurrect_a_deleted_snapshot(hass: HomeAssistant):
    """The mark, not the newest stored date, is what makes a deletion stick."""
    entry = await _setup(hass, **{
        CONF_TARIFFS: [BASE_TARIFF],
        CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01",
    })
    assert [t["effective_from"] for t in entry.options[CONF_TARIFFS]] == ["2024-12-12"]


async def test_setup_adds_a_snapshot_newer_than_the_mark(hass: HomeAssistant):
    """An older mark means a release has since shipped newer tariffs."""
    entry = await _setup(hass, **{
        CONF_TARIFFS: [BASE_TARIFF],
        CONF_TARIFFS_SEEDED_THROUGH: "2025-01-01",
    })
    assert [t["effective_from"] for t in entry.options[CONF_TARIFFS]] == [
        "2024-12-12", "2026-01-01", "2026-02-01",
    ]


async def test_malformed_stored_tariffs_do_not_break_setup(hass: HomeAssistant):
    entry = await _setup(hass, **{
        CONF_TARIFFS: [{"effective_from": "not-a-date"}],
        CONF_TARIFFS_SEEDED_THROUGH: "2026-02-01",
    })
    assert hass.states.get("sensor.agere_total_cost") is not None
