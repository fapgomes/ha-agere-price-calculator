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
    CONF_SOURCE, CONF_TARIFFS, CONF_TAXES, CONF_VAT_RATE, CONF_WASTE,
    CONF_WATER, DOMAIN,
)
from .entry_options import (
    format_decimal, readings_from_options, readings_to_options,
    tariffs_from_options, tariffs_to_options,
)
from .readings import SOURCE_MANUAL, Reading
from .tariffs import Tariff, TariffPeriod

NEW_READING = "new"
NEW_TARIFF = "new"

_TARIFF_VALUE_FIELDS = (
    "water_availability", "sanitation_drainage", "sanitation_availability",
    "waste_variable", "waste_fixed", "tax_water", "tax_sanitation",
    "tax_waste_mgmt",
)

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
        self._selected_tariff: str | None = None

    # --- menu ---

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["readings", "next_reading", "tariffs", "components"],
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
        placeholders: dict[str, str] = {}

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
            except InvalidOperation:
                errors["base"] = "invalid_number"
            except ValueError as err:
                # Surface the log's own message: it names both conflicting
                # readings, which is what tells the user what went wrong.
                errors["base"] = "invalid_reading"
                placeholders["error"] = str(err)
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
            step_id="reading_edit", data_schema=schema, errors=errors,
            description_placeholders=placeholders,
        )

    # --- tariffs ---

    async def async_step_tariffs(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._selected_tariff = user_input["tariff"]
            return await self.async_step_tariff_edit()

        choices = _tariff_choices(tariffs_from_options(self._entry.options))
        choices.append(
            selector.SelectOptionDict(value=NEW_TARIFF, label="➕ New effective date")
        )
        return self.async_show_form(
            step_id="tariffs",
            data_schema=vol.Schema({
                vol.Required("tariff"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=choices, mode="dropdown")
                )
            }),
        )

    async def async_step_tariff_edit(self, user_input: dict[str, Any] | None = None):
        schedule = tariffs_from_options(self._entry.options)
        existing = next(
            (p for p in schedule.periods
             if p.effective_from.isoformat() == self._selected_tariff),
            None,
        )
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            new_schedule = None
            try:
                if user_input.get("delete") and existing is not None:
                    new_schedule = schedule.remove(existing.effective_from)
                else:
                    # Remove first, then insert: never build an intermediate
                    # schedule that would fail validation on its own.
                    base = (
                        schedule.remove(existing.effective_from)
                        if existing else schedule
                    )
                    new_schedule = base.set(_tariff_from_form(user_input))
            except (InvalidOperation, ValueError) as err:
                errors["base"] = "invalid_tariff"
                placeholders["error"] = str(err)
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        **self._entry.options,
                        CONF_TARIFFS: tariffs_to_options(new_schedule),
                    },
                )

        # Copy forward: the newest snapshot for a new entry, its own values when
        # editing. A new tariff always succeeds the latest one.
        source = existing.tariff if existing else schedule.at(schedule.latest)
        default_date = existing.effective_from.isoformat() if existing else None
        return self.async_show_form(
            step_id="tariff_edit",
            data_schema=_tariff_schema(source, default_date),
            errors=errors,
            description_placeholders=placeholders,
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


def _tariff_choices(schedule) -> list:
    """Dropdown rows, newest first, each annotated with what it changed."""
    periods = schedule.periods
    labels = {}
    for index, period in enumerate(periods):
        previous = periods[index - 1].tariff if index else None
        changed = _changed_fields(period.tariff, previous)
        label = period.effective_from.isoformat()
        if changed:
            label += " · " + ", ".join(changed)
        labels[period.effective_from] = label
    return [
        selector.SelectOptionDict(
            value=p.effective_from.isoformat(), label=labels[p.effective_from]
        )
        for p in reversed(periods)
    ]


def _changed_fields(tariff: Tariff, previous: Tariff | None) -> list[str]:
    if previous is None:
        return ["base"]
    changed = []
    if (tariff.water_tier_prices, tariff.water_tier_bounds) != (
        previous.water_tier_prices, previous.water_tier_bounds
    ):
        changed.append("water")
    for name in _TARIFF_VALUE_FIELDS:
        if getattr(tariff, name) != getattr(previous, name):
            changed.append(name.replace("_", " "))
    return changed


def _tariff_schema(source: Tariff, default_date: str | None) -> vol.Schema:
    """Text fields, not numbers: prices carry six decimals on the invoice and a
    NumberSelector routes them through float. Decimal from text is exact, and it
    is the convention vat_rate already uses.
    """
    def suggest(value):
        return {
            "suggested_value": None if value is None else format_decimal(value)
        }

    fields: dict = {
        vol.Required("effective_from",
                     description={"suggested_value": default_date}):
            selector.DateSelector(),
        vol.Required("water_tier_bounds", description={
            "suggested_value": ",".join(str(b) for b in source.water_tier_bounds)
        }): str,
    }
    for index, price in enumerate(source.water_tier_prices, start=1):
        key = f"water_tier_price_{index}"
        # The top tier may be unknown, so it is the only optional price.
        marker = (
            vol.Optional if index == len(source.water_tier_prices) else vol.Required
        )
        fields[marker(key, description=suggest(price))] = str
    for name in _TARIFF_VALUE_FIELDS:
        fields[vol.Required(name, description=suggest(getattr(source, name)))] = str
    fields[vol.Required("delete", default=False)] = bool
    return vol.Schema(fields)


def _tariff_from_form(user_input: dict[str, Any]) -> TariffPeriod:
    bounds_raw = str(user_input["water_tier_bounds"]).replace(" ", "")
    try:
        bounds = tuple(int(b) for b in bounds_raw.split(",") if b)
    except ValueError as err:
        raise ValueError(
            f"tier bounds must be whole numbers separated by commas, got "
            f"{user_input['water_tier_bounds']!r}"
        ) from err

    prices = []
    for index in range(1, len(bounds) + 2):
        raw = str(user_input.get(f"water_tier_price_{index}", "") or "").strip()
        prices.append(None if not raw else _parse(raw, f"water_tier_price_{index}"))

    values = {
        name: _parse(str(user_input[name]).strip(), name)
        for name in _TARIFF_VALUE_FIELDS
    }
    return TariffPeriod(
        effective_from=date.fromisoformat(user_input["effective_from"]),
        tariff=Tariff(
            water_tier_bounds=bounds, water_tier_prices=tuple(prices), **values
        ),
    )


def _parse(raw: str, field: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as err:
        raise ValueError(f"{field}: {raw!r} is not a number") from err
