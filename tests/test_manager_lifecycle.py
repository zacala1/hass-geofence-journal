from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal.listener import GeofenceTrackerListener
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import Meters, Seconds
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.errors import (
    StorageClosedError,
)
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from tests.test_runtime_fixtures import runtime_resources, seed_runtime_resources

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import SQLConnection
    from homeassistant.core import HomeAssistant


def _settings(path: Path) -> Settings:
    return Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )


def _seed(path: Path) -> None:
    resources = runtime_resources(
        enter_seconds=0,
        exit_seconds=0,
        cooldown_seconds=0,
    )
    with SQLiteStore(path) as store:
        seed_runtime_resources(store, resources)


def _set_tracker(hass: HomeAssistant, latitude: float) -> None:
    hass.states.async_set(
        "person.fixture",
        "home" if latitude == 0.0 else "away",
        {
            ATTR_LATITUDE: latitude,
            ATTR_LONGITUDE: 0.0,
            ATTR_GPS_ACCURACY: 5.0,
        },
    )


async def test_startup_sync_failure_commits_no_generation_and_closes_store(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "startup-generation.db"
    _seed(path)
    _set_tracker(hass, 0.01)
    manager = GeofenceJournalManager(hass, _settings(path))

    async def fail_sync(_listener: GeofenceTrackerListener) -> None:
        detail = "injected startup sync failure"
        raise RuntimeError(detail)

    monkeypatch.setattr(
        GeofenceTrackerListener,
        "async_sync_existing_states",
        fail_sync,
    )

    with pytest.raises(RuntimeError, match="injected startup sync failure"):
        await manager.async_start()

    assert manager.listener_entity_ids == ()
    with pytest.raises(StorageClosedError):
        _ = await manager.store.async_run_operation(
            lambda connection: connection.execute("SELECT 1").fetchone()
        )


async def test_startup_cleanup_failure_stops_published_generation_and_store(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "startup-cleanup.db"
    _seed(path)
    _set_tracker(hass, 0.01)
    manager = GeofenceJournalManager(hass, _settings(path))

    def fail_cleanup(_connection: SQLConnection) -> None:
        detail = "injected runtime cleanup failure"
        raise sqlite3.IntegrityError(detail)

    monkeypatch.setattr(
        "custom_components.geofence_journal.manager.delete_inactive_runtime_states",
        fail_cleanup,
    )

    with pytest.raises(sqlite3.IntegrityError, match="runtime cleanup failure"):
        await manager.async_start()

    assert manager.listener_entity_ids == ()
    with pytest.raises(StorageClosedError):
        _ = await manager.store.async_run_operation(
            lambda connection: connection.execute("SELECT 1").fetchone()
        )


async def test_refresh_sync_failure_preserves_previous_generation(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "refresh-generation.db"
    _seed(path)
    _set_tracker(hass, 0.01)
    manager = GeofenceJournalManager(hass, _settings(path))
    await manager.async_start()

    async def fail_sync(_listener: GeofenceTrackerListener) -> None:
        detail = "injected staged sync failure"
        raise RuntimeError(detail)

    monkeypatch.setattr(
        GeofenceTrackerListener,
        "async_sync_existing_states",
        fail_sync,
    )

    with pytest.raises(RuntimeError, match="injected staged sync failure"):
        await manager.async_refresh_resources()

    monkeypatch.undo()
    assert manager.listener_entity_ids == ("person.fixture",)
    _set_tracker(hass, 0.0)
    await hass.async_block_till_done()
    await manager.async_stop()

    with SQLiteStore(path) as store:
        assert store.event_count() == 1
