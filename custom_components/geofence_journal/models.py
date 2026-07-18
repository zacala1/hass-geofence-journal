"""Typed domain contracts shared by Geofence Journal subsystems."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, NewType, Protocol

if TYPE_CHECKING:
    from datetime import datetime

TrackerId = NewType("TrackerId", str)
PlaceId = NewType("PlaceId", str)
JournalId = NewType("JournalId", str)
RuleId = NewType("RuleId", str)
EventId = NewType("EventId", str)
Meters = NewType("Meters", float)
Seconds = NewType("Seconds", int)


@unique
class TrackerKind(StrEnum):
    """Home Assistant entity domains supported as trackers."""

    PERSON = "person"
    DEVICE_TRACKER = "device_tracker"


@unique
class PlaceKind(StrEnum):
    """Supported place-definition sources."""

    COORDINATE = "coordinates"
    HA_ZONE = "ha_zone"


@unique
class PresenceState(StrEnum):
    """A rule's confirmed relationship to its place."""

    INSIDE = "inside"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


@unique
class LocationEventType(StrEnum):
    """Event kinds available in the v0.1.0 contract."""

    ENTER = "enter"
    EXIT = "exit"
    MANUAL = "manual"


@unique
class EventStatus(StrEnum):
    """Review state retained with an immutable event row."""

    CONFIRMED = "confirmed"
    EXCLUDED = "excluded"


@unique
class LocationSource(StrEnum):
    """Origin of an accepted location or event."""

    GPS = "gps"
    HA_ZONE = "ha_zone"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Coordinates:
    """A geographic point in decimal degrees."""

    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class TrackerLocation:
    """Normalized tracker observation at the Home Assistant boundary."""

    entity_id: str
    observed_at: datetime
    coordinates: Coordinates
    accuracy_m: Meters | None
    raw_state: str | None


@dataclass(frozen=True, slots=True)
class TrackerDefinition:
    """A configured Home Assistant tracker entity."""

    tracker_id: TrackerId
    entity_id: str
    kind: TrackerKind
    name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class CoordinatePlace:
    """A place with fixed coordinates and radius."""

    place_id: PlaceId
    name: str
    center: Coordinates
    radius_m: Meters


@dataclass(frozen=True, slots=True)
class ZonePlace:
    """A place whose geometry is read from a Home Assistant zone entity."""

    place_id: PlaceId
    name: str
    entity_id: str


type PlaceDefinition = CoordinatePlace | ZonePlace


@dataclass(frozen=True, slots=True)
class JournalDefinition:
    """A named event journal."""

    journal_id: JournalId
    name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """Typed linkage and thresholds for one tracker/place pair."""

    rule_id: RuleId
    tracker_id: TrackerId
    place_id: PlaceId
    journal_id: JournalId
    enabled: bool
    enter_confirmation_seconds: Seconds
    exit_confirmation_seconds: Seconds
    cooldown_seconds: Seconds
    exit_margin_meters: Meters
    max_gps_accuracy_meters: Meters


@dataclass(frozen=True, slots=True)
class LocationEvent:
    """Persisted geofence or manual journal event."""

    event_id: EventId
    journal_id: JournalId
    rule_id: RuleId | None
    tracker_id: TrackerId
    place_id: PlaceId
    event_type: LocationEventType
    occurred_at: datetime
    confirmed_at: datetime
    coordinates: Coordinates | None
    accuracy_m: Meters | None
    source: LocationSource
    status: EventStatus
    note: str | None
    created_at: datetime
    updated_at: datetime


class Clock(Protocol):
    """Wall and monotonic time capability used by deterministic runtimes."""

    def utc_now(self) -> datetime:
        """Return a timezone-aware UTC instant."""
        ...

    def monotonic(self) -> float:
        """Return process-local monotonic seconds."""
        ...


class DistanceCalculator(Protocol):
    """Distance capability injectable into pure geofence evaluation."""

    def meters_between(self, origin: Coordinates, destination: Coordinates) -> Meters:
        """Return the surface distance between two points in meters."""
        ...


class Storage(Protocol):
    """Lifecycle capability for the future persistence adapter."""

    async def async_open(self) -> None:
        """Open and migrate storage."""

    async def async_close(self) -> None:
        """Drain accepted work and close storage."""
