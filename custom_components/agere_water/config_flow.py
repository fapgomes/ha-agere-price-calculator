"""Config and options flow for AGERE Water Price."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_INCLUDE_VAT, CONF_RESET_DAY, CONF_SANITATION, CONF_SOURCE,
    CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DEFAULT_RESET_DAY,
    DOMAIN,
)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Required(CONF_RESET_DAY, default=DEFAULT_RESET_DAY): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=28, mode="box")
        ),
    }
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_RESET_DAY, default=options.get(CONF_RESET_DAY, DEFAULT_RESET_DAY)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=28, mode="box")),
            vol.Required(CONF_WATER, default=options.get(CONF_WATER, True)): bool,
            vol.Required(CONF_SANITATION, default=options.get(CONF_SANITATION, True)): bool,
            vol.Required(CONF_WASTE, default=options.get(CONF_WASTE, True)): bool,
            vol.Required(CONF_TAXES, default=options.get(CONF_TAXES, True)): bool,
            vol.Required(CONF_INCLUDE_VAT, default=options.get(CONF_INCLUDE_VAT, True)): bool,
            vol.Required(CONF_VAT_RATE, default=options.get(CONF_VAT_RATE, "0.06")): str,
        }
    )


class AgereWaterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            reset_day = int(user_input[CONF_RESET_DAY])
            return self.async_create_entry(
                title="AGERE Water Price",
                data={CONF_SOURCE: user_input[CONF_SOURCE]},
                options={
                    CONF_RESET_DAY: reset_day,
                    CONF_WATER: True, CONF_SANITATION: True,
                    CONF_WASTE: True, CONF_TAXES: True,
                    CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
                },
            )
        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AgereWaterOptionsFlow(config_entry)


class AgereWaterOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            user_input[CONF_RESET_DAY] = int(user_input[CONF_RESET_DAY])
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(dict(self._entry.options))
        )
