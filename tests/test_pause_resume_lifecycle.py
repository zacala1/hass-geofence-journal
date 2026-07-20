from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal.lifecycle import RuntimePauseTokenError
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import Meters, Seconds
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.errors import StorageClosedError
from tests.test_runtime_fixtures import runtime_resources, seed_runtime_resources

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.lifecycle import RuntimePauseHandle
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


async def _started_manager(hass: HomeAssistant, path: Path) -> GeofenceJournalManager:
    with SQLiteStore(path) as store:
        seed_runtime_resources(store, runtime_resources())
    manager = GeofenceJournalManager(hass, _settings(path))
    await manager.async_start()
    return manager


async def _raise_during_pause(manager: GeofenceJournalManager) -> None:
    async with manager.pause_and_drain():
        detail = "maintenance failed"
        raise sqlite3.OperationalError(detail)


async def test_nested_pause_resumes_only_after_final_handle(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    manager = await _started_manager(hass, tmp_path / "nested-pause.db")
    assert manager.listener_entity_ids == ("person.fixture",)

    first = await manager.async_pause("maintenance-one")
    second = await manager.async_pause("maintenance-two")
    assert manager.listener_entity_ids == ()

    await manager.async_resume(first)
    assert manager.listener_entity_ids == ()

    await manager.async_resume(second)
    assert manager.listener_entity_ids == ("person.fixture",)
    await manager.async_stop()


async def test_stale_pause_handle_raises_token_error(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    manager = await _started_manager(hass, tmp_path / "stale-pause.db")
    handle = await manager.async_pause("single-use")
    await manager.async_resume(handle)

    with pytest.raises(RuntimePauseTokenError):
        await manager.async_resume(handle)

    assert manager.listener_entity_ids == ("person.fixture",)
    await manager.async_stop()


async def test_pause_and_drain_preserves_primary_exception_when_resume_fails(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = await _started_manager(hass, tmp_path / "dual-failure.db")

    async def fail_resume(_handle: RuntimePauseHandle) -> None:
        raise StorageClosedError

    monkeypatch.setattr(manager, "async_resume", fail_resume)

    with pytest.raises(sqlite3.OperationalError, match="maintenance failed") as raised:
        await _raise_during_pause(manager)

    assert any("runtime resume also failed" in note for note in raised.value.__notes__)
    monkeypatch.undo()
    await manager.async_stop()
