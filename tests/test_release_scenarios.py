from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import Meters, Seconds, ZonePlace
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from pytest_homeassistant_custom_component.common import async_capture_events
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    RecoveryClock,
    RecoveryScheduler,
    runtime_resources,
    seed_runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

TRACKER_ENTITY_ID: Final = "person.fixture"
ZONE_ENTITY_ID: Final = "zone.release_office"


def _settings(path: Path) -> Settings:
    return Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )


def _tracker_attributes(latitude: float, longitude: float) -> dict[str, float]:
    return {
        ATTR_LATITUDE: latitude,
        ATTR_LONGITUDE: longitude,
        ATTR_GPS_ACCURACY: 5,
    }


async def test_restart_during_pending_enter_commits_exactly_once(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    database_path = tmp_path / "pending-restart.db"
    resources = runtime_resources(
        enter_seconds=120,
        exit_seconds=180,
        cooldown_seconds=300,
    )
    with SQLiteStore(database_path) as store:
        seed_runtime_resources(store, resources)
    hass.states.async_set(
        TRACKER_ENTITY_ID,
        "away",
        _tracker_attributes(latitude=0.01, longitude=0.0),
    )
    events = async_capture_events(hass, "geofence_journal_event")
    clock = RecoveryClock(RUNTIME_START)
    first_scheduler = RecoveryScheduler(clock)
    first_manager = GeofenceJournalManager(
        hass,
        _settings(database_path),
        clock=clock,
        scheduler=first_scheduler,
    )

    await first_manager.async_start()
    hass.states.async_set(
        TRACKER_ENTITY_ID,
        "home",
        _tracker_attributes(latitude=0.0, longitude=0.0),
    )
    await hass.async_block_till_done()
    assert len(first_scheduler.calls) == 1
    stale_callback = first_scheduler.calls[0].callback
    await first_scheduler.advance(60)
    await first_manager.async_stop()

    second_scheduler = RecoveryScheduler(clock)
    second_manager = GeofenceJournalManager(
        hass,
        _settings(database_path),
        clock=clock,
        scheduler=second_scheduler,
    )
    await second_manager.async_start()
    await stale_callback()
    await second_scheduler.advance(60)
    await hass.async_block_till_done()
    await second_manager.async_stop()

    with SQLiteStore(database_path) as store:
        assert store.event_count() == 1
    assert len(events) == 1
    assert events[0].data["event_type"] == "enter"


async def test_zone_change_waits_for_next_tracker_observation(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    database_path = tmp_path / "dynamic-zone.db"
    base = runtime_resources(
        enter_seconds=0,
        exit_seconds=0,
        cooldown_seconds=0,
    )
    resources = replace(
        base,
        place=ZonePlace(
            place_id=base.place.place_id,
            name="Release office",
            entity_id=ZONE_ENTITY_ID,
        ),
    )
    with SQLiteStore(database_path) as store:
        seed_runtime_resources(store, resources)
    hass.states.async_set(
        ZONE_ENTITY_ID,
        "0",
        {ATTR_LATITUDE: 0.0, ATTR_LONGITUDE: 0.0, "radius": 100},
    )
    hass.states.async_set(
        TRACKER_ENTITY_ID,
        "away",
        _tracker_attributes(latitude=0.01, longitude=0.0),
    )
    events = async_capture_events(hass, "geofence_journal_event")
    manager = GeofenceJournalManager(hass, _settings(database_path))

    await manager.async_start()
    hass.states.async_set(
        ZONE_ENTITY_ID,
        "0",
        {ATTR_LATITUDE: 0.01, ATTR_LONGITUDE: 0.0, "radius": 100},
    )
    await hass.async_block_till_done()
    with SQLiteStore(database_path) as store:
        assert store.event_count() == 0

    hass.states.async_set(
        TRACKER_ENTITY_ID,
        "office",
        _tracker_attributes(latitude=0.01, longitude=0.0),
    )
    await hass.async_block_till_done()
    await manager.async_stop()

    with SQLiteStore(database_path) as store:
        assert store.event_count() == 1
    assert len(events) == 1
    assert events[0].data["event_type"] == "enter"
    assert not {"latitude", "longitude", "accuracy_m"} & set(events[0].data)
