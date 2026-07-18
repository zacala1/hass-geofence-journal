"""Home Assistant location-boundary normalization and zone resolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from custom_components.geofence_journal.location import (
    IgnoredObservation,
    IgnoreReason,
    NormalizedObservation,
    RawTrackerObservation,
    ResolvedPlace,
    ZoneSnapshot,
    normalize_tracker_observation,
    resolve_place,
)
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    Meters,
    PlaceId,
    ZonePlace,
)

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


class MutableZoneLookup:
    """Mutable fake proving every resolution performs a fresh lookup."""

    snapshot: ZoneSnapshot | None
    calls: int

    def __init__(self, snapshot: ZoneSnapshot | None) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def get_zone(self, entity_id: str) -> ZoneSnapshot | None:
        _ = entity_id
        self.calls += 1
        return self.snapshot


def raw_observation(
    *,
    latitude: float | str | None = 37.0,
    longitude: float | str | None = 127.0,
    accuracy: float | str | None = 10.0,
    state: str | None = "not_home",
    observed_at: datetime = NOW,
) -> RawTrackerObservation:
    """Build raw Home Assistant boundary data."""
    return RawTrackerObservation(
        observed_at=observed_at,
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy,
        state=state,
    )


@pytest.mark.parametrize(
    ("sample", "reason"),
    [
        (raw_observation(latitude=None), IgnoreReason.MISSING_COORDINATES),
        (raw_observation(longitude=None), IgnoreReason.MISSING_COORDINATES),
        (raw_observation(latitude="north"), IgnoreReason.INVALID_COORDINATES),
        (raw_observation(latitude=float("nan")), IgnoreReason.INVALID_COORDINATES),
        (raw_observation(longitude=float("inf")), IgnoreReason.INVALID_COORDINATES),
        (raw_observation(latitude=90.0001), IgnoreReason.INVALID_COORDINATES),
        (raw_observation(longitude=-180.0001), IgnoreReason.INVALID_COORDINATES),
        (raw_observation(state=None), IgnoreReason.INVALID_STATE),
        (raw_observation(state="unknown"), IgnoreReason.INVALID_STATE),
        (raw_observation(state="unavailable"), IgnoreReason.INVALID_STATE),
    ],
)
def test_tracker_observation_is_ignored_when_boundary_data_is_invalid(
    sample: RawTrackerObservation, reason: IgnoreReason
) -> None:
    # Given malformed Home Assistant state data
    # When the boundary normalizer parses it
    result = normalize_tracker_observation(sample, last_accepted_at=None)

    # Then it reports why the sample must not advance runtime state
    assert result == IgnoredObservation(reason=reason, observed_at=NOW)


def test_tracker_observation_is_ignored_when_older_than_last_accepted() -> None:
    # Given a sample older than the last accepted tracker timestamp
    sample = raw_observation(observed_at=NOW - timedelta(microseconds=1))

    # When it is normalized
    result = normalize_tracker_observation(sample, last_accepted_at=NOW)

    # Then the stale sample is ignored
    assert result == IgnoredObservation(
        reason=IgnoreReason.STALE_TIMESTAMP,
        observed_at=NOW - timedelta(microseconds=1),
    )


def test_equal_timestamp_is_not_older_and_is_accepted() -> None:
    # Given a sample at exactly the last accepted timestamp
    # When it is normalized
    result = normalize_tracker_observation(raw_observation(), last_accepted_at=NOW)

    # Then it remains a valid observation
    assert isinstance(result, NormalizedObservation)


def test_coordinate_place_resolves_without_zone_lookup() -> None:
    # Given a fixed coordinate place
    place = CoordinatePlace(
        place_id=PlaceId("place-1"),
        name="Office",
        center=Coordinates(37.0, 127.0),
        radius_m=Meters(200.0),
    )
    zones = MutableZoneLookup(snapshot=None)

    # When the place is resolved for a tracker observation
    result = resolve_place(place, zones, observed_at=NOW)

    # Then configured geometry is used and HA zones are not queried
    assert result == ResolvedPlace(center=place.center, radius_m=place.radius_m)
    assert zones.calls == 0


def test_zone_geometry_is_read_fresh_on_each_tracker_observation() -> None:
    # Given a zone-backed place whose HA geometry can change
    place = ZonePlace(PlaceId("place-1"), "Home", "zone.home")
    zones = MutableZoneLookup(ZoneSnapshot(37.0, 127.0, 100.0))

    # When separate tracker observations resolve before and after an edit
    first = resolve_place(place, zones, observed_at=NOW)
    zones.snapshot = ZoneSnapshot(38.0, 128.0, 300.0)
    second = resolve_place(place, zones, observed_at=NOW + timedelta(seconds=1))

    # Then the next tracker observation sees the new geometry
    assert first == ResolvedPlace(Coordinates(37.0, 127.0), Meters(100.0))
    assert second == ResolvedPlace(Coordinates(38.0, 128.0), Meters(300.0))
    assert zones.calls == 2


def test_missing_zone_is_ignored() -> None:
    # Given a configured zone entity that is absent from HA state
    place = ZonePlace(PlaceId("place-1"), "Home", "zone.home")

    # When a tracker observation asks for current geometry
    result = resolve_place(place, MutableZoneLookup(None), observed_at=NOW)

    # Then no synthetic location state is produced
    assert result == IgnoredObservation(
        reason=IgnoreReason.MISSING_ZONE, observed_at=NOW
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        ZoneSnapshot(float("nan"), 127.0, 100.0),
        ZoneSnapshot(37.0, 181.0, 100.0),
        ZoneSnapshot(37.0, 127.0, 0.0),
        ZoneSnapshot(37.0, 127.0, float("inf")),
    ],
)
def test_invalid_zone_geometry_is_ignored(snapshot: ZoneSnapshot) -> None:
    # Given malformed current zone geometry
    place = ZonePlace(PlaceId("place-1"), "Home", "zone.home")

    # When a tracker observation resolves it
    result = resolve_place(place, MutableZoneLookup(snapshot), observed_at=NOW)

    # Then invalid geometry cannot reach distance evaluation
    assert result == IgnoredObservation(
        reason=IgnoreReason.INVALID_ZONE, observed_at=NOW
    )
