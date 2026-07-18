"""Typed state contract shared by Geofence Journal diagnostic entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from custom_components.geofence_journal.const import DOMAIN, TITLE

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class UnloadedEntityState:
    """The integration runtime is not loaded."""


@dataclass(frozen=True, slots=True)
class HealthyEntityState:
    """The database is reachable; the optional event instant is UTC-aware."""

    last_event_at: datetime | None


@dataclass(frozen=True, slots=True)
class DatabaseErrorEntityState:
    """The runtime is loaded but its database is unavailable."""


type GeofenceJournalEntityState = (
    UnloadedEntityState | HealthyEntityState | DatabaseErrorEntityState
)

LAST_EVENT_UNIQUE_ID: Final = f"{DOMAIN}_last_event"
HEALTHY_UNIQUE_ID: Final = f"{DOMAIN}_healthy"
SERVICE_DEVICE_IDENTIFIER: Final = (DOMAIN, DOMAIN)


class EntityStateProvider(Protocol):
    """Publish immutable diagnostic snapshots to the fixed HA entities."""

    @property
    def entity_state(self) -> GeofenceJournalEntityState:
        """Return the current immutable entity snapshot."""
        ...

    def async_subscribe_entity_state(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Notify after snapshot replacement and return an unsubscribe callback."""
        ...


type GeofenceJournalConfigEntry = ConfigEntry[EntityStateProvider]


def service_device_info() -> DeviceInfo:
    """Return fresh metadata for the integration's single service device."""
    return DeviceInfo(
        entry_type=DeviceEntryType.SERVICE,
        identifiers={SERVICE_DEVICE_IDENTIFIER},
        name=TITLE,
    )
