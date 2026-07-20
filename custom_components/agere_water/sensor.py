"""Sensor platform for AGERE Water Price."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .calculator import calcular, marginal_price
from .const import (
    CONF_INCLUDE_VAT, CONF_RESET_DAY, CONF_SANITATION, CONF_SOURCE,
    CONF_TAXES, CONF_VAT_RATE, CONF_WASTE, CONF_WATER, DEFAULT_RESET_DAY,
    DEFAULT_VAT_RATE, DOMAIN, CalcConfig, Tariff,
)
from .cycle import CycleManager, CycleState

_STORE_VERSION = 1


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
    """Owns cycle state, persistence, and the latest computed breakdown."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.source = entry.data[CONF_SOURCE]
        self.config = _calc_config(dict(entry.options))
        reset_day = int(entry.options.get(CONF_RESET_DAY, DEFAULT_RESET_DAY))
        self._store = Store(hass, _STORE_VERSION, f"{DOMAIN}_{entry.entry_id}")
        self._manager = CycleManager(reset_day)
        self.breakdown = None
        self.marginal = Decimal("0")
        self.consumption = Decimal("0")
        self.days_elapsed = 0
        self.cycle_days = 0
        self._listeners: list = []

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self._manager = CycleManager(
                self._manager.reset_day,
                CycleState(
                    cycle_start=date.fromisoformat(stored["cycle_start"]),
                    baseline=Decimal(stored["baseline"]),
                ),
            )

    async def _async_save(self) -> None:
        st = self._manager.state
        if st is not None:
            await self._store.async_save(
                {"cycle_start": st.cycle_start.isoformat(), "baseline": str(st.baseline)}
            )

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
        today = dt_util.now().date()
        prev = self._manager.state
        self._manager.update(today, meter_total)
        self.consumption = self._manager.consumption(meter_total)
        self.days_elapsed = self._manager.days_elapsed(today)
        self.cycle_days = self._manager.cycle_length_days()
        self.breakdown = calcular(self.consumption, self.cycle_days, self.config)
        self.marginal = marginal_price(self.consumption, self.cycle_days, self.config)
        if self._manager.state is not prev:
            self.hass.async_create_task(self._async_save())
        for cb in self._listeners:
            cb()


async def async_setup_entry(hass, entry, async_add_entities):
    data = _AgereData(hass, entry)
    await data.async_load()

    entities: list[SensorEntity] = [
        AgereTotalCostSensor(data),
        AgereMarginalPriceSensor(data),
        AgereCycleConsumptionSensor(data),
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


class AgereTotalCostSensor(_AgereBase):
    _attr_name = "AGERE total cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"

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
        if not bd:
            return None
        active_tiers = [line.index + 1 for line in bd.lines if line.qty > 0]
        return {
            "base_without_vat": float(bd.base_without_vat),
            "vat": float(bd.vat),
            "cycle_consumption_m3": float(self._data.consumption),
            "water": float(bd.water),
            "sanitation": float(bd.sanitation),
            "waste": float(bd.waste),
            "taxes": float(bd.taxes),
            "days_elapsed": self._data.days_elapsed,
            "billing_days": self._data.cycle_days,
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
        if self._data.breakdown is None:
            return None
        return float(self._data.consumption)


class AgereComponentCostSensor(_AgereBase):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"

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
