"""Diagnostic sensor platform for Geofence Journal."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from custom_components.geofence_journal.entity_state import (
    LAST_EVENT_UNIQUE_ID,
    DatabaseErrorEntityState,
    EntityStateProvider,
    GeofenceJournalConfigEntry,
    GeofenceJournalEntityState,
    HealthyEntityState,
    UnloadedEntityState,
    service_device_info,
)

if TYPE_CHECKING:
    from datetime import date, datetime
    from decimal import Decimal

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import StateType


class LastEventSensor(SensorEntity):
    """Timestamp of the most recently recorded event."""

    _attr_available: bool
    _attr_device_info: DeviceInfo | None
    _attr_entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name: bool = True
    _attr_name: str | None = None
    _attr_native_value: StateType | date | datetime | Decimal
    _attr_should_poll: bool = False
    _attr_translation_key: str | None = "last_event"
    _attr_unique_id: str | None = LAST_EVENT_UNIQUE_ID
    _provider: EntityStateProvider

    def __init__(self, provider: EntityStateProvider) -> None:
        """Initialize the sensor from the provider's current snapshot."""
        self._provider = provider
        self._attr_device_class: SensorDeviceClass | None = SensorDeviceClass.TIMESTAMP
        self._attr_device_info = service_device_info()
        self._apply_entity_state(self.entity_state)

    @property
    def entity_state(self) -> GeofenceJournalEntityState:
        """Return the provider's current immutable snapshot."""
        return self._provider.entity_state

    @property
    @override
    def suggested_object_id(self) -> str:
        """Return the stable default object ID."""
        return "last_event"

    def _apply_entity_state(self, state: GeofenceJournalEntityState) -> None:
        """Apply readable timestamp and availability attributes."""
        match state:
            case HealthyEntityState(last_event_at=last_event_at):
                self._attr_available = True
                self._attr_native_value = last_event_at
            case DatabaseErrorEntityState() | UnloadedEntityState():
                self._attr_available = False
                self._attr_native_value = None
            case unreachable:
                assert_never(unreachable)

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to push updates while this entity is loaded."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._provider.async_subscribe_entity_state(
                self._async_handle_entity_state_update
            )
        )

    @callback
    def _async_handle_entity_state_update(self) -> None:
        """Apply and publish the provider's new snapshot."""
        self._apply_entity_state(self.entity_state)
        self.async_write_ha_state()


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: GeofenceJournalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the one fixed last-event sensor."""
    async_add_entities([LastEventSensor(entry.runtime_data)])
