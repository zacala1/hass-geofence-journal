"""Normalize tracker observations and resolve current place geometry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from math import isfinite
from typing import TYPE_CHECKING, Final, Protocol, assert_never

from .models import (
    CoordinatePlace,
    Coordinates,
    Meters,
    PlaceDefinition,
    ZonePlace,
)

if TYPE_CHECKING:
    from datetime import datetime

type RawNumber = float | str | None

MIN_LATITUDE: Final = -90.0
MAX_LATITUDE: Final = 90.0
MIN_LONGITUDE: Final = -180.0
MAX_LONGITUDE: Final = 180.0


@unique
class IgnoreReason(StrEnum):
    """Reason an input cannot advance confirmed or pending runtime state."""

    MISSING_COORDINATES = "missing_coordinates"
    INVALID_COORDINATES = "invalid_coordinates"
    INVALID_ACCURACY = "invalid_accuracy"
    EXCESSIVE_ACCURACY = "excessive_accuracy"
    INVALID_STATE = "invalid_state"
    INVALID_TIMESTAMP = "invalid_timestamp"
    STALE_TIMESTAMP = "stale_timestamp"
    MISSING_ZONE = "missing_zone"
    INVALID_ZONE = "invalid_zone"
    INVALID_DISTANCE = "invalid_distance"


@dataclass(frozen=True, slots=True)
class RawTrackerObservation:
    """Untrusted Home Assistant tracker attributes at the adapter boundary."""

    observed_at: datetime
    latitude: RawNumber
    longitude: RawNumber
    accuracy_m: RawNumber
    state: str | None


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    """A tracker sample safe for pure distance evaluation."""

    observed_at: datetime
    coordinates: Coordinates
    accuracy_m: Meters | None


@dataclass(frozen=True, slots=True)
class IgnoredObservation:
    """An unusable sample that leaves all runtime state unchanged."""

    reason: IgnoreReason
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ZoneSnapshot:
    """Current geometry read from one Home Assistant zone state."""

    latitude: RawNumber
    longitude: RawNumber
    radius_m: RawNumber


@dataclass(frozen=True, slots=True)
class ResolvedPlace:
    """Validated geometry used for one tracker observation only."""

    center: Coordinates
    radius_m: Meters


class ZoneLookup(Protocol):
    """Fresh Home Assistant zone-state lookup capability."""

    def get_zone(self, entity_id: str) -> ZoneSnapshot | None:
        """Read current zone geometry, or return missing."""
        ...


def normalize_tracker_observation(
    raw: RawTrackerObservation, *, last_accepted_at: datetime | None
) -> NormalizedObservation | IgnoredObservation:
    """Parse one tracker update without mutating runtime state."""
    timestamp_error = _timestamp_error(raw.observed_at, last_accepted_at)
    if timestamp_error is not None:
        return IgnoredObservation(timestamp_error, raw.observed_at)
    if raw.state is None or raw.state.strip().lower() in {"", "unknown", "unavailable"}:
        return IgnoredObservation(IgnoreReason.INVALID_STATE, raw.observed_at)
    if raw.latitude is None or raw.longitude is None:
        return IgnoredObservation(IgnoreReason.MISSING_COORDINATES, raw.observed_at)
    latitude = _finite_float(raw.latitude)
    longitude = _finite_float(raw.longitude)
    if (
        latitude is None
        or longitude is None
        or not MIN_LATITUDE <= latitude <= MAX_LATITUDE
        or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
    ):
        return IgnoredObservation(IgnoreReason.INVALID_COORDINATES, raw.observed_at)
    if raw.accuracy_m is None:
        accuracy = None
    else:
        parsed_accuracy = _finite_float(raw.accuracy_m)
        if parsed_accuracy is None or parsed_accuracy < 0.0:
            return IgnoredObservation(IgnoreReason.INVALID_ACCURACY, raw.observed_at)
        accuracy = Meters(parsed_accuracy)
    return NormalizedObservation(
        observed_at=raw.observed_at,
        coordinates=Coordinates(latitude, longitude),
        accuracy_m=accuracy,
    )


def resolve_place(
    place: PlaceDefinition, zones: ZoneLookup, *, observed_at: datetime
) -> ResolvedPlace | IgnoredObservation:
    """Resolve fixed or freshly read zone geometry for a tracker observation."""
    match place:
        case CoordinatePlace(center=center, radius_m=radius_m):
            if not _valid_geometry(center.latitude, center.longitude, radius_m):
                return IgnoredObservation(IgnoreReason.INVALID_ZONE, observed_at)
            return ResolvedPlace(center=center, radius_m=radius_m)
        case ZonePlace(entity_id=entity_id):
            snapshot = zones.get_zone(entity_id)
            if snapshot is None:
                return IgnoredObservation(IgnoreReason.MISSING_ZONE, observed_at)
            if (
                snapshot.latitude is None
                or snapshot.longitude is None
                or snapshot.radius_m is None
            ):
                return IgnoredObservation(IgnoreReason.INVALID_ZONE, observed_at)
            latitude = _finite_float(snapshot.latitude)
            longitude = _finite_float(snapshot.longitude)
            radius = _finite_float(snapshot.radius_m)
            if (
                latitude is None
                or longitude is None
                or radius is None
                or not _valid_geometry(latitude, longitude, radius)
            ):
                return IgnoredObservation(IgnoreReason.INVALID_ZONE, observed_at)
            return ResolvedPlace(Coordinates(latitude, longitude), Meters(radius))
        case unreachable:
            assert_never(unreachable)


def _timestamp_error(
    observed_at: datetime, last_accepted_at: datetime | None
) -> IgnoreReason | None:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return IgnoreReason.INVALID_TIMESTAMP
    if last_accepted_at is not None and observed_at < last_accepted_at:
        return IgnoreReason.STALE_TIMESTAMP
    return None


def _finite_float(value: float | str) -> float | None:
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return None
    return parsed if isfinite(parsed) else None


def _valid_geometry(latitude: float, longitude: float, radius_m: float) -> bool:
    return (
        isfinite(latitude)
        and isfinite(longitude)
        and isfinite(radius_m)
        and MIN_LATITUDE <= latitude <= MAX_LATITUDE
        and MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
        and radius_m > 0.0
    )
