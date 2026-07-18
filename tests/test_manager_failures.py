from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal.entity_state import (
    DatabaseErrorEntityState,
    HealthyEntityState,
)
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import Meters, PresenceState, Seconds
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.errors import StorageClosedError
from custom_components.geofence_journal.storage.resources import upsert_rule
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    open_runtime_engine,
    recovery_observation,
    runtime_resources,
    seed_runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

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


async def _raise_during_pause(manager: GeofenceJournalManager) -> None:
    async with manager.pause_and_drain():
        detail = "maintenance failed"
        raise sqlite3.OperationalError(detail)


async def test_start_failure_closes_the_open_worker(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = GeofenceJournalManager(hass, _settings(tmp_path / "start-failure.db"))

    async def fail_refresh() -> None:
        raise StorageClosedError

    monkeypatch.setattr(manager, "async_refresh_resources", fail_refresh)
    with pytest.raises(StorageClosedError):
        await manager.async_start()

    assert manager.settings.database_path.endswith("start-failure.db")
    assert isinstance(manager.entity_state, DatabaseErrorEntityState)
    with pytest.raises(StorageClosedError):
        _ = await manager.store.async_run_operation(
            lambda connection: connection.execute("SELECT 1").fetchone()
        )


async def test_refresh_deactivates_a_rule_removed_from_the_active_graph(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    path = tmp_path / "disabled-rule.db"
    resources = runtime_resources()
    with SQLiteStore(path) as store:
        seed_runtime_resources(store, resources)
    manager = GeofenceJournalManager(hass, _settings(path))
    await manager.async_start()
    disabled = replace(resources.rule, enabled=False)

    await manager.store.async_run_operation(
        lambda connection: upsert_rule(
            connection, disabled, RUNTIME_START, name="Disabled"
        )
    )
    await manager.async_refresh_resources()

    assert manager.listener_entity_ids == ()
    assert await manager.store.async_runtime_state(str(resources.rule.rule_id)) is None
    await manager.async_stop()


async def test_maintenance_failure_resumes_with_explicit_database_error(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    manager = GeofenceJournalManager(hass, _settings(tmp_path / "pause-failure.db"))
    await manager.async_start()

    with pytest.raises(sqlite3.OperationalError, match="maintenance failed"):
        await _raise_during_pause(manager)

    assert isinstance(manager.entity_state, DatabaseErrorEntityState)
    await manager.async_stop()


async def test_start_recovers_the_latest_committed_event_timestamp(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    path = tmp_path / "latest-event.db"
    resources = runtime_resources(
        enter_seconds=0,
        exit_seconds=0,
        cooldown_seconds=0,
    )
    with SQLiteStore(path) as store:
        seed_runtime_resources(store, resources)
    engine, store, _scheduler = await open_runtime_engine(
        path, resources.rule, RUNTIME_START
    )
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, RUNTIME_START)
    )
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, RUNTIME_START)
    )
    await store.async_close()
    manager = GeofenceJournalManager(hass, _settings(path))

    await manager.async_start()

    state = manager.entity_state
    assert isinstance(state, HealthyEntityState)
    assert state.last_event_at == RUNTIME_START
    await manager.async_stop()
