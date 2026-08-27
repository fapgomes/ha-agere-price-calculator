"""Config and options flow for AGERE Water Price."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_INCLUDE_VAT, CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SANITATION,
    CONF_SOURCE, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DOMAIN,
)
from .entry_options import readings_from_options, readings_to_options
from .readings import SOURCE_MANUAL, Reading

NEW_READING = "new"

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    }
)


def _components_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WATER, default=options.get(CONF_WATER, True)): bool,
            vol.Required(CONF_SANITATION, default=options.get(CONF_SANITATION, True)): bool,
            vol.Required(CONF_WASTE, default=options.get(CONF_WASTE, True)): bool,
            vol.Required(CONF_TAXES, default=options.get(CONF_TAXES, True)): bool,
            vol.Required(CONF_INCLUDE_VAT, default=options.get(CONF_INCLUDE_VAT, True)): bool,
            vol.Required(CONF_VAT_RATE, default=options.get(CONF_VAT_RATE, "0.06")): str,
        }
    )


class AgereWaterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(
                title="AGERE Water Price",
                data={CONF_SOURCE: user_input[CONF_SOURCE]},
                options={
                    CONF_WATER: True, CONF_SANITATION: True,
                    CONF_WASTE: True, CONF_TAXES: True,
                    CONF_INCLUDE_VAT: True, CONF_VAT_RATE: "0.06",
                    CONF_READINGS: [], CONF_NEXT_READING_DATE: None,
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
        self._selected: str | None = None

    # --- menu ---

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["readings", "next_reading", "components"],
        )

    # --- tariff components ---

    async def async_step_components(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self._entry.options, **user_input}
            )
        return self.async_show_form(
            step_id="components",
            data_schema=_components_schema(dict(self._entry.options)),
        )

    # --- readings ---

    async def async_step_readings(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._selected = user_input["reading"]
            return await self.async_step_reading_edit()

        log = readings_from_options(self._entry.options)
        cycles = {c.end: c for c in log.closed_cycles()}
        choices = []
        for reading in reversed(log.readings):
            cycle = cycles.get(reading.date)
            label = f"{reading.date.isoformat()} · {reading.m3} m³"
            if cycle is not None:
                label += f" · {cycle.days} d · {cycle.consumption} m³"
            choices.append(
                selector.SelectOptionDict(value=reading.date.isoformat(), label=label)
            )
        choices.append(
            selector.SelectOptionDict(value=NEW_READING, label="➕ New reading")
        )
        return self.async_show_form(
            step_id="readings",
            data_schema=vol.Schema({
                vol.Required("reading"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=choices, mode="dropdown")
                )
            }),
        )

    async def async_step_reading_edit(self, user_input: dict[str, Any] | None = None):
        log = readings_from_options(self._entry.options)
        existing = next(
            (r for r in log.readings if r.date.isoformat() == self._selected), None
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            new_log = None
            try:
                if user_input.get("delete") and existing is not None:
                    new_log = log.remove(existing.date)
                else:
                    # Remove first, then insert: never build an intermediate log
                    # that would fail validation on its own.
                    base = log.remove(existing.date) if existing else log
                    new_log = base.set(Reading(
                        date=date.fromisoformat(user_input["date"]),
                        m3=Decimal(str(user_input["m3"])),
                        source=SOURCE_MANUAL,
                    ))
            except (InvalidOperation, ValueError):
                errors["base"] = "invalid_reading"
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        **self._entry.options,
                        CONF_READINGS: readings_to_options(new_log),
                    },
                )

        default_date = existing.date.isoformat() if existing else None
        default_m3 = float(existing.m3) if existing else None
        schema = vol.Schema({
            vol.Required("date", description={"suggested_value": default_date}):
                selector.DateSelector(),
            vol.Required("m3", description={"suggested_value": default_m3}):
                selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, step="any", mode="box")
                ),
            vol.Required("delete", default=False): bool,
        })
        return self.async_show_form(
            step_id="reading_edit", data_schema=schema, errors=errors
        )

    # --- next reading date ---

    async def async_step_next_reading(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            value = None if user_input.get("clear") else user_input["date"]
            return self.async_create_entry(
                title="",
                data={**self._entry.options, CONF_NEXT_READING_DATE: value},
            )
        current = self._entry.options.get(CONF_NEXT_READING_DATE)
        return self.async_show_form(
            step_id="next_reading",
            data_schema=vol.Schema({
                vol.Required("date", description={"suggested_value": current}):
                    selector.DateSelector(),
                vol.Required("clear", default=False): bool,
            }),
        )
