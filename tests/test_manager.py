from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from custom_components.geofence_journal.entity_state import DatabaseErrorEntityState
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    RuleDefinition,
    RuleId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
    ZonePlace,
)
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.errors import StorageClosedError
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
)
from tests.test_runtime_fixtures import RecoveryClock, RecoveryScheduler

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

NOW: Final = datetime(2026, 7, 18, 3, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")
RULE_ID: Final = RuleId("00000000-0000-4000-8000-000000000004")


def _settings(path: Path) -> Settings:
    return Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )


def _seed(
    path: Path,
    place: CoordinatePlace | ZonePlace,
    *,
    enter_seconds: int = 0,
) -> None:
    with SQLiteStore(path) as store:
        store.upsert_tracker(
            TrackerDefinition(
                tracker_id=TRACKER_ID,
                entity_id="person.alice",
                kind=TrackerKind.PERSON,
                name="Alice",
                enabled=True,
            ),
            NOW,
        )
        store.upsert_place(place, NOW)
        store.upsert_journal(
            JournalDefinition(journal_id=JOURNAL_ID, name="Visits", enabled=True),
            NOW,
        )
        store.upsert_rule(
            RuleDefinition(
                rule_id=RULE_ID,
                tracker_id=TRACKER_ID,
                place_id=PLACE_ID,
                journal_id=JOURNAL_ID,
                enabled=True,
                enter_confirmation_seconds=Seconds(enter_seconds),
                exit_confirmation_seconds=Seconds(0),
                cooldown_seconds=Seconds(0),
                exit_margin_meters=Meters(50),
                max_gps_accuracy_meters=Meters(100),
            ),
            NOW,
        )


async def test_start_synchronizes_existing_tracker_as_no_event_baseline(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: an enabled coordinate rule and an already-present tracker state.
    path = tmp_path / "baseline.db"
    _seed(
        path,
        CoordinatePlace(
            place_id=PLACE_ID,
            name="Home",
            center=Coordinates(37.5, 127.0),
            radius_m=Meters(100),
        ),
    )
    hass.states.async_set(
        "person.alice",
        "home",
        {
            ATTR_LATITUDE: 37.5,
            ATTR_LONGITUDE: 127.0,
            ATTR_GPS_ACCURACY: 5,
        },
    )
    manager = GeofenceJournalManager(hass, _settings(path))

    # When: the manager opens, recovers, and installs its listener.
    await manager.async_start()
    await manager.async_stop()

    # Then: startup persisted a baseline but did not fabricate an event.
    with SQLiteStore(path) as store:
        state = store.runtime_state(str(RULE_ID))
        assert state is not None
        assert store.event_count() == 0


async def test_missing_tracker_and_zone_keep_setup_runnable(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: an enabled zone rule whose HA entities do not exist yet.
    path = tmp_path / "missing.db"
    _seed(
        path,
        ZonePlace(place_id=PLACE_ID, name="Office", entity_id="zone.office"),
    )
    manager = GeofenceJournalManager(hass, _settings(path))

    # When: the manager starts without either state.
    await manager.async_start()

    # Then: setup succeeds and retains the configured tracker listener.
    assert manager.listener_entity_ids == ("person.alice",)
    await manager.async_stop()


async def test_refresh_replaces_listener_without_duplicate_entity_ids(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: a running manager with one active tracker rule.
    path = tmp_path / "refresh.db"
    _seed(
        path,
        CoordinatePlace(
            place_id=PLACE_ID,
            name="Home",
            center=Coordinates(37.5, 127.0),
            radius_m=Meters(100),
        ),
    )
    manager = GeofenceJournalManager(hass, _settings(path))
    await manager.async_start()

    # When: resource refresh is requested repeatedly.
    await manager.async_refresh_resources()
    await manager.async_refresh_resources()

    # Then: the current generation contains one unique subscription target.
    assert manager.listener_entity_ids == ("person.alice",)
    await manager.async_stop()


async def test_refresh_storage_failure_marks_database_unhealthy(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: a started manager whose database worker becomes unavailable.
    path = tmp_path / "unhealthy.db"
    manager = GeofenceJournalManager(hass, _settings(path))
    await manager.async_start()
    await manager.store.async_close()

    # When: a resource refresh reaches the closed storage boundary.
    with pytest.raises(StorageClosedError):
        await manager.async_refresh_resources()

    # Then: diagnostic providers observe the database failure.
    assert isinstance(manager.entity_state, DatabaseErrorEntityState)
    await manager.async_stop()


async def test_zone_geometry_change_applies_on_next_tracker_sample(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: an outside baseline for a zone-backed rule.
    path = tmp_path / "dynamic-zone.db"
    _seed(
        path,
        ZonePlace(place_id=PLACE_ID, name="Office", entity_id="zone.office"),
    )
    hass.states.async_set(
        "zone.office",
        "0",
        {ATTR_LATITUDE: 0.0, ATTR_LONGITUDE: 0.0, "radius": 100},
    )
    hass.states.async_set(
        "person.alice",
        "away",
        {
            ATTR_LATITUDE: 0.01,
            ATTR_LONGITUDE: 0.0,
            ATTR_GPS_ACCURACY: 5,
        },
    )
    events = async_capture_events(hass, "geofence_journal_event")
    manager = GeofenceJournalManager(hass, _settings(path))
    await manager.async_start()

    # When: the zone moves over the tracker and a new tracker state arrives.
    hass.states.async_set(
        "zone.office",
        "0",
        {ATTR_LATITUDE: 0.01, ATTR_LONGITUDE: 0.0, "radius": 100},
    )
    hass.states.async_set(
        "person.alice",
        "office",
        {
            ATTR_LATITUDE: 0.01,
            ATTR_LONGITUDE: 0.0,
            ATTR_GPS_ACCURACY: 5,
        },
    )
    await hass.async_block_till_done()
    await manager.async_stop()

    # Then: fresh geometry confirms one event with no coordinate fields.
    with SQLiteStore(path) as store:
        assert store.event_count() == 1
    assert len(events) == 1
    assert events[0].data["event_type"] == "enter"
    assert "latitude" not in events[0].data
    assert "longitude" not in events[0].data
    assert "accuracy_m" not in events[0].data


async def test_refresh_during_pending_transition_commits_exactly_once(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: an outside baseline and a pending 120-second enter.
    path = tmp_path / "pending-refresh.db"
    _seed(
        path,
        CoordinatePlace(
            place_id=PLACE_ID,
            name="Home",
            center=Coordinates(0.0, 0.0),
            radius_m=Meters(100),
        ),
        enter_seconds=120,
    )
    hass.states.async_set(
        "person.alice",
        "away",
        {ATTR_LATITUDE: 0.01, ATTR_LONGITUDE: 0.0, ATTR_GPS_ACCURACY: 5},
    )
    clock = RecoveryClock(NOW)
    scheduler = RecoveryScheduler(clock)
    manager = GeofenceJournalManager(
        hass, _settings(path), clock=clock, scheduler=scheduler
    )
    await manager.async_start()
    hass.states.async_set(
        "person.alice",
        "home",
        {ATTR_LATITUDE: 0.0, ATTR_LONGITUDE: 0.0, ATTR_GPS_ACCURACY: 5},
    )
    await hass.async_block_till_done()

    # When: resources reload and the recovered deadline becomes due.
    await manager.async_refresh_resources()
    await scheduler.advance(121)
    await manager.async_stop()

    # Then: the cancelled old callback is harmless and recovery commits once.
    with SQLiteStore(path) as store:
        assert store.event_count() == 1
