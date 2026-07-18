"""Pure, deterministic geofence distance and hysteresis evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import TYPE_CHECKING, Final

from .location import (
    IgnoredObservation,
    IgnoreReason,
    NormalizedObservation,
    ResolvedPlace,
)
from .models import Coordinates, Meters, PresenceState

if TYPE_CHECKING:
    from datetime import datetime

    from .models import DistanceCalculator

EARTH_RADIUS_M: Final = 6_371_000.0


@dataclass(frozen=True, slots=True)
class EvaluatedObservation:
    """Accepted observation and its deterministic presence classification."""

    presence: PresenceState
    distance_m: Meters
    observed_at: datetime
    coordinates: Coordinates
    accuracy_m: Meters | None


@dataclass(frozen=True, slots=True)
class PersistenceProjection:
    """Privacy-aware fields allowed to cross into persistence."""

    presence: PresenceState
    distance_m: Meters
    observed_at: datetime
    coordinates: Coordinates | None
    accuracy_m: Meters | None


@dataclass(frozen=True, slots=True)
class EvaluationThresholds:
    """Rule thresholds used by one pure geofence evaluation."""

    exit_margin_m: Meters
    max_accuracy_m: Meters


class HaversineDistance:
    """Behavior-equivalent great-circle distance calculator in meters."""

    def meters_between(self, origin: Coordinates, destination: Coordinates) -> Meters:
        """Return the shortest spherical surface distance."""
        latitude_delta = radians(destination.latitude - origin.latitude)
        longitude_delta = radians(destination.longitude - origin.longitude)
        origin_latitude = radians(origin.latitude)
        destination_latitude = radians(destination.latitude)
        haversine = sin(latitude_delta / 2.0) ** 2 + (
            cos(origin_latitude)
            * cos(destination_latitude)
            * sin(longitude_delta / 2.0) ** 2
        )
        central_angle = 2.0 * asin(sqrt(min(1.0, max(0.0, haversine))))
        return Meters(EARTH_RADIUS_M * central_angle)


def evaluate_geofence(
    observation: NormalizedObservation,
    place: ResolvedPlace,
    confirmed: PresenceState,
    thresholds: EvaluationThresholds,
    *,
    distance: DistanceCalculator,
) -> EvaluatedObservation | IgnoredObservation:
    """Classify one valid observation using exact hysteresis boundaries."""
    accuracy = observation.accuracy_m
    if accuracy is not None and (not isfinite(accuracy) or accuracy < 0.0):
        return IgnoredObservation(
            IgnoreReason.INVALID_ACCURACY, observation.observed_at
        )
    if accuracy is not None and accuracy > thresholds.max_accuracy_m:
        return IgnoredObservation(
            IgnoreReason.EXCESSIVE_ACCURACY, observation.observed_at
        )
    measured = distance.meters_between(observation.coordinates, place.center)
    if not isfinite(measured) or measured < 0.0:
        return IgnoredObservation(
            IgnoreReason.INVALID_DISTANCE, observation.observed_at
        )
    if measured <= place.radius_m:
        presence = PresenceState.INSIDE
    elif measured >= place.radius_m + thresholds.exit_margin_m:
        presence = PresenceState.OUTSIDE
    else:
        presence = confirmed
    return EvaluatedObservation(
        presence=presence,
        distance_m=measured,
        observed_at=observation.observed_at,
        coordinates=observation.coordinates,
        accuracy_m=accuracy,
    )


def project_for_persistence(
    evaluated: EvaluatedObservation, *, store_coordinates: bool
) -> PersistenceProjection:
    """Apply coordinate privacy without changing the evaluation result."""
    return PersistenceProjection(
        presence=evaluated.presence,
        distance_m=evaluated.distance_m,
        observed_at=evaluated.observed_at,
        coordinates=evaluated.coordinates if store_coordinates else None,
        accuracy_m=evaluated.accuracy_m,
    )
