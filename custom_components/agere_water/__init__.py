"""AGERE Water Price integration."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from .const import (
    CONF_READINGS, CONF_TARIFFS, CONF_TARIFFS_SEEDED_THROUGH, DOMAIN,
    PLATFORMS,
)
from .readings import SOURCE_AUTO

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Removed in v2; only referenced to strip it from migrated entries.
_LEGACY_RESET_DAY = "reset_day"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Imported lazily: this package's __init__ must stay importable without
    # Home Assistant, so the pure engine tests can run standalone.
    from .services import SERVICE_SET_READING, async_setup_services

    if not hass.services.has_service(DOMAIN, SERVICE_SET_READING):
        async_setup_services(hass)
    _seed_tariffs(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


def _seed_tariffs(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Write the built-in tariffs, and later only ones newer than the mark.

    Nothing stored is ever overwritten, so an edited or deleted snapshot stays
    as the user left it. Options are written only when something is added,
    because writing them reloads the entry.
    """
    from .entry_options import (
        seeded_through_from_options, tariffs_from_options, tariffs_to_options,
    )
    from .tariffs import BUILTIN_SCHEDULE

    try:
        stored = tariffs_from_options(entry.options)
        mark = seeded_through_from_options(entry.options)
    except ValueError as err:
        _LOGGER.error(
            "Stored AGERE tariffs are invalid (%s); leaving them untouched and "
            "falling back to the built-in schedule. Fix them under Settings -> "
            "Devices & Services -> AGERE -> Configure -> Tariffs", err,
        )
        return

    merged, new_mark = stored.merge_newer(BUILTIN_SCHEDULE, mark)
    already = entry.options.get(CONF_TARIFFS) or []
    if len(merged) == len(already) and mark == new_mark:
        return

    hass.config_entries.async_update_entry(entry, options={
        **entry.options,
        CONF_TARIFFS: tariffs_to_options(merged),
        CONF_TARIFFS_SEEDED_THROUGH: new_mark.isoformat(),
    })
    _LOGGER.info(
        "Seeded the AGERE tariff schedule through %s (%d entries)",
        new_mark.isoformat(), len(merged),
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 (fixed reset day + Store baseline) -> v2 (reading log in options)."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        # Imported lazily: this package's __init__ must stay importable without
        # Home Assistant, so the pure engine tests can run standalone.
        from homeassistant.helpers.storage import Store

        store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        stored = await store.async_load()
        readings: list[dict[str, str]] = []
        if stored and stored.get("cycle_start") and stored.get("baseline") is not None:
            # The v1 cycle started on cycle_start, so the equivalent reading is
            # the day before it, carrying the same baseline value.
            when = date.fromisoformat(stored["cycle_start"]) - timedelta(days=1)
            readings = [{
                "date": when.isoformat(),
                "m3": str(stored["baseline"]),
                "source": SOURCE_AUTO,
            }]
        options = {
            k: v for k, v in entry.options.items() if k != _LEGACY_RESET_DAY
        }
        options[CONF_READINGS] = readings
        hass.config_entries.async_update_entry(entry, options=options, version=2)
        await store.async_remove()
        _LOGGER.info(
            "Migrated AGERE entry to the reading log (%d reading(s) carried over)",
            len(readings),
        )
    return True
