"""Pure geofence evaluator behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from custom_components.geofence_journal.geofence import (
    EvaluatedObservation,
    EvaluationThresholds,
    HaversineDistance,
    evaluate_geofence,
    project_for_persistence,
)
from custom_components.geofence_journal.location import (
    IgnoredObservation,
    IgnoreReason,
    NormalizedObservation,
    ResolvedPlace,
)
from custom_components.geofence_journal.models import Coordinates, Meters, PresenceState


@dataclass(frozen=True, slots=True)
class FixedDistance:
    """Return a deterministic distance for threshold tests."""

    distance_m: float

    def meters_between(self, origin: Coordinates, destination: Coordinates) -> Meters:
        _ = (origin, destination)
        return Meters(self.distance_m)


NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
ORIGIN = Coordinates(latitude=37.0, longitude=127.0)
PLACE = ResolvedPlace(center=Coordinates(37.1, 127.1), radius_m=Meters(200.0))
THRESHOLDS = EvaluationThresholds(
    exit_margin_m=Meters(50.0), max_accuracy_m=Meters(100.0)
)


def observation(*, accuracy: float | None = 10.0) -> NormalizedObservation:
    """Build a valid normalized observation."""
    return NormalizedObservation(
        observed_at=NOW,
        coordinates=ORIGIN,
        accuracy_m=None if accuracy is None else Meters(accuracy),
    )


@pytest.mark.parametrize(
    ("distance_m", "confirmed", "expected"),
    [
        (199.0, PresenceState.UNKNOWN, PresenceState.INSIDE),
        (200.0, PresenceState.OUTSIDE, PresenceState.INSIDE),
        (201.0, PresenceState.INSIDE, PresenceState.INSIDE),
        (225.0, PresenceState.INSIDE, PresenceState.INSIDE),
        (249.0, PresenceState.INSIDE, PresenceState.INSIDE),
        (201.0, PresenceState.OUTSIDE, PresenceState.OUTSIDE),
        (249.0, PresenceState.OUTSIDE, PresenceState.OUTSIDE),
        (225.0, PresenceState.UNKNOWN, PresenceState.UNKNOWN),
        (250.0, PresenceState.INSIDE, PresenceState.OUTSIDE),
        (251.0, PresenceState.INSIDE, PresenceState.OUTSIDE),
    ],
)
def test_presence_when_distance_crosses_exact_hysteresis_boundaries(
    distance_m: float, confirmed: PresenceState, expected: PresenceState
) -> None:
    # Given a 200 m place with a 50 m exit margin
    # When the observation is evaluated at the requested distance
    result = evaluate_geofence(
        observation(),
        PLACE,
        confirmed,
        THRESHOLDS,
        distance=FixedDistance(distance_m),
    )

    # Then equality and band retention follow the specified formula
    assert isinstance(result, EvaluatedObservation)
    assert result.presence is expected


@pytest.mark.parametrize(
    ("accuracy", "reason"),
    [
        (100.0001, IgnoreReason.EXCESSIVE_ACCURACY),
        (float("nan"), IgnoreReason.INVALID_ACCURACY),
        (float("inf"), IgnoreReason.INVALID_ACCURACY),
        (-1.0, IgnoreReason.INVALID_ACCURACY),
    ],
)
def test_observation_is_ignored_when_accuracy_is_unusable(
    accuracy: float, reason: IgnoreReason
) -> None:
    # Given an observation whose accuracy cannot satisfy the rule
    # When it is evaluated
    result = evaluate_geofence(
        observation(accuracy=accuracy),
        PLACE,
        PresenceState.INSIDE,
        THRESHOLDS,
        distance=FixedDistance(0.0),
    )

    # Then the caller receives an ignored result, not a replacement state
    assert result == IgnoredObservation(
        reason=reason,
        observed_at=NOW,
    )


def test_missing_accuracy_is_accepted_and_preserved_as_missing() -> None:
    # Given a valid observation without an accuracy attribute
    # When it is evaluated inside the place
    result = evaluate_geofence(
        observation(accuracy=None),
        PLACE,
        PresenceState.OUTSIDE,
        THRESHOLDS,
        distance=FixedDistance(100.0),
    )

    # Then evaluation succeeds and records accuracy as missing
    assert isinstance(result, EvaluatedObservation)
    assert result.accuracy_m is None
    assert result.presence is PresenceState.INSIDE


def test_privacy_projection_omits_coordinates_without_affecting_evaluation() -> None:
    # Given an accepted inside evaluation with coordinates
    evaluated = evaluate_geofence(
        observation(),
        PLACE,
        PresenceState.OUTSIDE,
        THRESHOLDS,
        distance=FixedDistance(50.0),
    )
    assert isinstance(evaluated, EvaluatedObservation)

    # When persistence privacy is disabled
    projection = project_for_persistence(evaluated, store_coordinates=False)

    # Then state and distance remain useful while coordinates are omitted
    assert projection.coordinates is None
    assert projection.presence is PresenceState.INSIDE
    assert projection.distance_m == Meters(50.0)


def test_haversine_handles_dateline_crossing() -> None:
    # Given points separated by 0.2 degrees across the antimeridian
    # When their surface distance is calculated
    result = HaversineDistance().meters_between(
        Coordinates(0.0, 179.9), Coordinates(0.0, -179.9)
    )

    # Then the short dateline route is used
    assert result == pytest.approx(22_239.0, rel=0.001)


def test_haversine_handles_near_pole_coordinates() -> None:
    # Given two longitudes close to the north pole
    # When their surface distance is calculated
    result = HaversineDistance().meters_between(
        Coordinates(89.9, 0.0), Coordinates(89.9, 180.0)
    )

    # Then the finite route across the pole is returned
    assert result == pytest.approx(22_239.0, rel=0.001)
