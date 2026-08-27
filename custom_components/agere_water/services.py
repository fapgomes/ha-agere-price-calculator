"""Services for managing the AGERE meter-reading log."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .calculator import calcular
from .const import CONF_NEXT_READING_DATE, CONF_READINGS, CONF_SOURCE, DOMAIN
from .entry_options import readings_from_options, readings_to_options
from .readings import SOURCE_MANUAL, Reading

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_READING = "set_reading"
SERVICE_REMOVE_READING = "remove_reading"
SERVICE_SET_NEXT_READING_DATE = "set_next_reading_date"

ATTR_CONFIG_ENTRY = "config_entry"
ATTR_DATE = "date"
ATTR_M3 = "m3"

_SET_READING_SCHEMA = vol.Schema({
    vol.Required(ATTR_DATE): cv.date,
    vol.Optional(ATTR_M3): vol.Coerce(float),
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
})

_REMOVE_READING_SCHEMA = vol.Schema({
    vol.Required(ATTR_DATE): cv.date,
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
})

_SET_NEXT_SCHEMA = vol.Schema({
    vol.Optional(ATTR_DATE): cv.date,
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
})


def _resolve_entry(hass: HomeAssistant, call: ServiceCall) -> ConfigEntry:
    entry_id = call.data.get(ATTR_CONFIG_ENTRY)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(f"unknown config_entry {entry_id}")
        return entry
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1:
        raise ServiceValidationError(
            "more than one AGERE entry is set up: pass config_entry to say which"
        )
    return entries[0]


async def _async_meter_value_on(
    hass: HomeAssistant, entry: ConfigEntry, when: date
) -> Decimal:
    """Last meter value recorded on `when`, from long-term statistics."""
    entity_id = entry.data[CONF_SOURCE]
    if "recorder" not in hass.config.components:
        raise ServiceValidationError(
            f"no meter value for {when.isoformat()}: the recorder is not "
            "running, so pass m3 explicitly (take it from the invoice)"
        )
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    start = dt_util.as_utc(datetime.combine(when, time.min))
    end = start + timedelta(days=1)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, "day", None,
        {"state"},
    )
    points = stats.get(entity_id) or []
    value = points[-1].get("state") if points else None
    if value is None:
        raise ServiceValidationError(
            f"no meter value for {when.isoformat()} in the history of "
            f"{entity_id}: pass m3 explicitly (take it from the invoice)"
        )
    return Decimal(str(value))


def _cycle_response(options: dict) -> dict:
    """Recompute the cycle ending on the newest reading, for the call response."""
    from .sensor import _calc_config

    log = readings_from_options(options)
    closed = log.closed_cycles()
    if not closed:
        return {}
    cycle = closed[-1]
    bd = calcular(cycle.consumption, cycle.days, _calc_config(options))
    return {
        "cycle": {
            "start": cycle.start.isoformat(),
            "end": cycle.end.isoformat(),
            "days": cycle.days,
            "consumption_m3": float(cycle.consumption),
            "total": float(bd.total),
            "water": float(bd.water),
            "sanitation": float(bd.sanitation),
            "waste": float(bd.waste),
            "taxes": float(bd.taxes),
            "vat": float(bd.vat),
        }
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the reading-log services once, at integration setup."""

    async def _async_set_reading(call: ServiceCall) -> dict:
        entry = _resolve_entry(hass, call)
        when: date = call.data[ATTR_DATE]
        if ATTR_M3 in call.data:
            try:
                m3 = Decimal(str(call.data[ATTR_M3]))
            except InvalidOperation as err:
                raise ServiceValidationError(f"invalid m3 value: {err}") from err
        else:
            m3 = await _async_meter_value_on(hass, entry, when)
        log = readings_from_options(entry.options)
        try:
            new_log = log.set(Reading(date=when, m3=m3, source=SOURCE_MANUAL))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        options = {**entry.options, CONF_READINGS: readings_to_options(new_log)}
        hass.config_entries.async_update_entry(entry, options=options)
        return _cycle_response(options)

    async def _async_remove_reading(call: ServiceCall) -> None:
        entry = _resolve_entry(hass, call)
        log = readings_from_options(entry.options)
        try:
            new_log = log.remove(call.data[ATTR_DATE])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_READINGS: readings_to_options(new_log)},
        )

    async def _async_set_next(call: ServiceCall) -> None:
        entry = _resolve_entry(hass, call)
        when: date | None = call.data.get(ATTR_DATE)
        log = readings_from_options(entry.options)
        if when is not None and log.last is not None:
            try:
                log.current_cycle(log.last.m3, when)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_NEXT_READING_DATE: when.isoformat() if when else None,
            },
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_READING, _async_set_reading,
        schema=_SET_READING_SCHEMA, supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_READING, _async_remove_reading,
        schema=_REMOVE_READING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_NEXT_READING_DATE, _async_set_next,
        schema=_SET_NEXT_SCHEMA,
    )
