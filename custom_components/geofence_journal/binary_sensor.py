"""Diagnostic binary sensor platform for Geofence Journal."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from custom_components.geofence_journal.entity_state import (
    HEALTHY_UNIQUE_ID,
    DatabaseErrorEntityState,
    EntityStateProvider,
    GeofenceJournalConfigEntry,
    GeofenceJournalEntityState,
    HealthyEntityState,
    UnloadedEntityState,
    service_device_info,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


class HealthyBinarySensor(BinarySensorEntity):
    """Whether the loaded integration can access its database."""

    _attr_available: bool
    _attr_device_info: DeviceInfo | None
    _attr_entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name: bool = True
    _attr_is_on: bool | None
    _attr_name: str | None = None
    _attr_should_poll: bool = False
    _attr_translation_key: str | None = "healthy"
    _attr_unique_id: str | None = HEALTHY_UNIQUE_ID
    _provider: EntityStateProvider

    def __init__(self, provider: EntityStateProvider) -> None:
        """Initialize the binary sensor from the current snapshot."""
        self._provider = provider
        self._attr_device_class: BinarySensorDeviceClass | None = (
            BinarySensorDeviceClass.CONNECTIVITY
        )
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
        return "healthy"

    def _apply_entity_state(self, state: GeofenceJournalEntityState) -> None:
        """Apply health and loaded availability attributes."""
        match state:
            case HealthyEntityState():
                self._attr_available = True
                self._attr_is_on = True
            case DatabaseErrorEntityState():
                self._attr_available = True
                self._attr_is_on = False
            case UnloadedEntityState():
                self._attr_available = False
                self._attr_is_on = False
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
    """Set up the one fixed health binary sensor."""
    async_add_entities([HealthyBinarySensor(entry.runtime_data)])
