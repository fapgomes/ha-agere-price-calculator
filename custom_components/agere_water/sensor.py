"""Sensor platform for AGERE Water Price."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from .calculator import calcular, marginal_price
from .const import (
    CONF_INCLUDE_VAT, CONF_READINGS, CONF_SANITATION, CONF_SOURCE, CONF_TAXES,
    CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DEFAULT_VAT_RATE, DOMAIN,
    CalcConfig, Tariff,
)
from .entry_options import (
    next_reading_date_from_options, readings_from_options, readings_to_options,
)
from .readings import (
    SOURCE_AUTO, Cycle, Reading, ReadingLog, days_elapsed, is_overdue,
)

_LOGGER = logging.getLogger(__name__)


def _calc_config(options: dict) -> CalcConfig:
    try:
        vat_rate = Decimal(str(options.get(CONF_VAT_RATE, DEFAULT_VAT_RATE)))
    except (InvalidOperation, TypeError):
        vat_rate = DEFAULT_VAT_RATE
    return CalcConfig(
        tariff=Tariff(),
        include_water=options.get(CONF_WATER, True),
        include_sanitation=options.get(CONF_SANITATION, True),
        include_waste=options.get(CONF_WASTE, True),
        include_taxes=options.get(CONF_TAXES, True),
        include_vat=options.get(CONF_INCLUDE_VAT, True),
        vat_rate=vat_rate,
    )


class _AgereData:
    """Owns the reading log and the latest computed breakdowns."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.source = entry.data[CONF_SOURCE]
        self.config = _calc_config(dict(entry.options))
        try:
            self.log = readings_from_options(entry.options)
        except ValueError as err:
            _LOGGER.error(
                "Stored AGERE readings are invalid (%s); starting from an empty "
                "log. Fix them in Settings -> Devices & Services -> AGERE -> "
                "Configure -> Readings", err,
            )
            self.log = ReadingLog()
        try:
            self.next_reading_date = next_reading_date_from_options(entry.options)
        except ValueError:
            _LOGGER.error("Stored next reading date is invalid; ignoring it")
            self.next_reading_date = None
        self.cycle: Cycle | None = None
        self.breakdown = None
        self.closed: list[tuple] = []
        self.marginal = Decimal("0")
        self.days_elapsed = 0
        self.overdue = False
        self._seeding = False
        self._listeners: list = []

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    @callback
    def recompute(self) -> None:
        state = self.hass.states.get(self.source)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return
        try:
            meter_total = Decimal(state.state)
        except InvalidOperation:
            return

        if len(self.log) == 0:
            self._seed(meter_total)
            return

        today = dt_util.now().date()
        self.cycle = self.log.current_cycle(meter_total, self.next_reading_date)
        self.days_elapsed = days_elapsed(self.cycle, today)
        self.overdue = is_overdue(self.cycle, today)
        self.breakdown = calcular(self.cycle.consumption, self.cycle.days, self.config)
        self.marginal = marginal_price(
            self.cycle.consumption, self.cycle.days, self.config
        )
        self.closed = [
            (c, calcular(c.consumption, c.days, self.config))
            for c in self.log.closed_cycles()
        ]
        for cb in self._listeners:
            cb()

    def _seed(self, meter_total: Decimal) -> None:
        """Write the first reading so later cycles have a starting point.

        Reproduces the previous behaviour: the first cycle is partial, because
        the baseline is captured now rather than at the real cycle start. The
        user replaces it with the reading from their latest invoice.
        """
        if self._seeding:
            return
        self._seeding = True
        seeded = ReadingLog([Reading(dt_util.now().date(), meter_total, SOURCE_AUTO)])
        options = {**self.entry.options, CONF_READINGS: readings_to_options(seeded)}
        self.hass.async_create_task(self._async_write_options(options))

    async def _async_write_options(self, options: dict) -> None:
        self.hass.config_entries.async_update_entry(self.entry, options=options)


async def async_setup_entry(hass, entry, async_add_entities):
    data = _AgereData(hass, entry)

    entities: list[SensorEntity] = [
        AgereTotalCostSensor(data),
        AgereMarginalPriceSensor(data),
        AgereCycleConsumptionSensor(data),
        AgereLastInvoiceSensor(data),
    ]
    for key, attr, name in (
        (CONF_WATER, "water", "Water cost"),
        (CONF_SANITATION, "sanitation", "Sanitation cost"),
        (CONF_WASTE, "waste", "Waste cost"),
        (CONF_TAXES, "taxes", "Taxes cost"),
    ):
        if entry.options.get(key, True):
            entities.append(AgereComponentCostSensor(data, attr, name))

    async_add_entities(entities)

    @callback
    def _on_source_change(event: Event) -> None:
        data.recompute()

    data.recompute()  # initial
    entry.async_on_unload(
        async_track_state_change_event(hass, [data.source], _on_source_change)
    )
    entry.async_on_unload(
        async_track_time_change(
            hass, lambda now: data.recompute(), hour=0, minute=0, second=30
        )
    )


class _AgereBase(SensorEntity):
    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, data: _AgereData) -> None:
        self._data = data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.entry.entry_id)},
            name="AGERE Water Price",
            manufacturer="AGERE",
        )

    async def async_added_to_hass(self) -> None:
        self._data.add_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class _AgereCycleMonetary(_AgereBase):
    """A cost that accumulates within one billing period, then starts over.

    `device_class: monetary` only accepts `state_class: total` — never
    `total_increasing` — so the per-period reset is expressed through
    `last_reset` instead of relying on the value dropping.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "EUR"

    @property
    def last_reset(self):
        cycle = self._data.cycle
        return dt_util.start_of_local_day(cycle.start) if cycle else None


class AgereTotalCostSensor(_AgereCycleMonetary):
    _attr_name = "AGERE total cost"

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_total_cost"
        self.entity_id = "sensor.agere_total_cost"

    @property
    def native_value(self):
        return float(self._data.breakdown.total) if self._data.breakdown else None

    @property
    def extra_state_attributes(self):
        bd = self._data.breakdown
        cycle = self._data.cycle
        if not bd or cycle is None:
            return None
        active_tiers = [line.index + 1 for line in bd.lines if line.qty > 0]
        return {
            "base_without_vat": float(bd.base_without_vat),
            "vat": float(bd.vat),
            "cycle_consumption_m3": float(cycle.consumption),
            "water": float(bd.water),
            "sanitation": float(bd.sanitation),
            "waste": float(bd.waste),
            "taxes": float(bd.taxes),
            "days_elapsed": self._data.days_elapsed,
            "billing_days": cycle.days,
            "billing_days_estimated": cycle.estimated,
            "cycle_start": cycle.start.isoformat(),
            "cycle_end": cycle.end.isoformat(),
            "cycle_overdue": self._data.overdue,
            "next_reading_date": (
                self._data.next_reading_date.isoformat()
                if self._data.next_reading_date else None
            ),
            "current_tier": active_tiers[-1] if active_tiers else 1,
            "tiers": [
                {
                    "tier": line.index + 1,
                    "m3": float(line.qty),
                    "eur_per_m3": float(line.rate),
                    "eur": float(line.value),
                }
                for line in bd.lines
            ],
        }


class AgereMarginalPriceSensor(_AgereBase):
    _attr_name = "AGERE marginal price"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/m³"

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_marginal_price"
        self.entity_id = "sensor.agere_marginal_price"

    @property
    def native_value(self):
        if self._data.breakdown is None:
            return None
        return float(self._data.marginal)


class AgereCycleConsumptionSensor(_AgereBase):
    _attr_name = "AGERE cycle consumption"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "m³"
    # Show fractional m³ (litre precision) so partial usage is visible while it
    # accrues. AGERE bills in whole m³, but this live sensor reflects the raw
    # metered difference.
    _attr_suggested_display_precision = 3

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_cycle_consumption"
        self.entity_id = "sensor.agere_cycle_consumption"

    @property
    def native_value(self):
        if self._data.cycle is None:
            return None
        return float(self._data.cycle.consumption)


class AgereComponentCostSensor(_AgereCycleMonetary):
    def __init__(self, data: _AgereData, attr: str, name: str) -> None:
        super().__init__(data)
        self._attr = attr
        self._attr_name = f"AGERE {name}"
        self._attr_unique_id = f"{data.entry.entry_id}_{attr}_cost"
        self.entity_id = f"sensor.agere_{attr}_cost"

    @property
    def native_value(self):
        bd = self._data.breakdown
        return float(getattr(bd, self._attr)) if bd else None


class AgereLastInvoiceSensor(_AgereBase):
    """Total of the most recent CLOSED cycle, plus the full derived history."""

    _attr_name = "AGERE last invoice"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, data: _AgereData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_last_invoice"
        self.entity_id = "sensor.agere_last_invoice"

    @property
    def native_value(self):
        if not self._data.closed:
            return None
        return float(self._data.closed[-1][1].total)

    @property
    def extra_state_attributes(self):
        return {
            "cycles": [
                {
                    "start": cycle.start.isoformat(),
                    "end": cycle.end.isoformat(),
                    "days": cycle.days,
                    "m3": float(cycle.consumption),
                    "total": float(bd.total),
                }
                for cycle, bd in self._data.closed
            ],
            "readings": readings_to_options(self._data.log),
        }
