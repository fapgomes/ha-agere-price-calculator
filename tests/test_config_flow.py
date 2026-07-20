import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.agere_water.const import (
    CONF_RESET_DAY, CONF_SOURCE, DOMAIN,
)


async def test_user_flow_creates_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SOURCE: "sensor.water_meter_total", CONF_RESET_DAY: 13},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE] == "sensor.water_meter_total"
    assert result["options"][CONF_RESET_DAY] == 13
