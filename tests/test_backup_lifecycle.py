from __future__ import annotations

from shutil import copyfile
from typing import TYPE_CHECKING, cast

import pytest
from custom_components.geofence_journal.backup import (
    BackupProcessDataError,
    async_post_backup,
    async_pre_backup,
)
from custom_components.geofence_journal.const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_STORE_COORDINATES,
    DOMAIN,
    TITLE,
)
from custom_components.geofence_journal.export import ExportRegistry
from custom_components.geofence_journal.lifecycle import RuntimePauseHandle
from custom_components.geofence_journal.process_data import IntegrationProcessData
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.db_types import required_integer
from custom_components.geofence_journal.storage.errors import StorageClosedError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    RecoveryClock,
    runtime_resources,
    seed_runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal import GeofenceJournalConfigEntry
    from custom_components.geofence_journal.storage.db_types import SQLConnection
    from homeassistant.core import HomeAssistant


def _entry(path: Path) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        data={
            CONF_STORE_COORDINATES: False,
            CONF_ENTER_CONFIRMATION_SECONDS: 0,
            CONF_EXIT_CONFIRMATION_SECONDS: 0,
            CONF_COOLDOWN_SECONDS: 0,
            CONF_EXIT_MARGIN_METERS: 50.0,
            CONF_DATABASE_PATH: str(path),
        },
    )


def _select_tracker_count(connection: SQLConnection) -> int:
    row = connection.execute("SELECT COUNT(*) FROM trackers").fetchone()
    assert row is not None
    return required_integer(row[0], field="tracker count")


def _process_data(hass: HomeAssistant) -> IntegrationProcessData:
    return IntegrationProcessData.model_validate(hass.data[DOMAIN])


async def _setup_seeded_entry(
    hass: HomeAssistant, path: Path
) -> GeofenceJournalConfigEntry:
    with SQLiteStore(path) as store:
        seed_runtime_resources(store, runtime_resources())
    entry = _entry(path)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return cast("GeofenceJournalConfigEntry", entry)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_backup_pre_and_post_pause_and_resume_loaded_manager(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    path = tmp_path / "backup-lifecycle.db"
    entry = await _setup_seeded_entry(hass, path)
    manager = entry.runtime_data
    assert manager.listener_entity_ids == ("person.fixture",)

    await async_pre_backup(hass)

    assert manager.listener_entity_ids == ()
    pause = _process_data(hass).backup_pause
    assert pause is not None
    await async_pre_backup(hass)
    assert _process_data(hass).backup_pause is pause
    with pytest.raises(StorageClosedError):
        _ = await manager.store.async_run_operation(_select_tracker_count)
    copied = tmp_path / "backup-copy.db"
    _ = copyfile(path, copied)
    with SQLiteStore(copied) as copied_store:
        assert copied_store.run_operation(_select_tracker_count) == 1

    await async_post_backup(hass)

    assert _process_data(hass).backup_pause is None
    assert manager.listener_entity_ids == ("person.fixture",)
    assert await manager.store.async_run_operation(_select_tracker_count) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_backup_hooks_noop_without_loaded_entry(hass: HomeAssistant) -> None:
    await async_pre_backup(hass)
    await async_post_backup(hass)

    assert DOMAIN not in hass.data


async def test_post_backup_clears_orphaned_pause_without_loaded_entry(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    process_data = IntegrationProcessData(
        exports=ExportRegistry(tmp_path / "exports", RecoveryClock(RUNTIME_START)),
        backup_pause=RuntimePauseHandle.create(reason="orphaned-backup"),
    )
    hass.data[DOMAIN] = process_data

    await async_post_backup(hass)

    assert _process_data(hass).backup_pause is None


async def test_malformed_backup_process_data_is_rejected(hass: HomeAssistant) -> None:
    hass.data[DOMAIN] = {"exports": "not-an-export-registry"}

    with pytest.raises(BackupProcessDataError) as raised:
        await async_post_backup(hass)

    assert str(raised.value) == "invalid Geofence Journal backup process data"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_loaded_manager_requires_process_data_before_backup(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    entry = await _setup_seeded_entry(hass, tmp_path / "backup-process-data.db")
    process_data = IntegrationProcessData.model_validate(hass.data.pop(DOMAIN))
    try:
        with pytest.raises(BackupProcessDataError):
            await async_pre_backup(hass)
    finally:
        hass.data[DOMAIN] = process_data
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_failed_post_keeps_pause_handle_for_retry(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = await _setup_seeded_entry(hass, tmp_path / "backup-retry.db")
    manager = entry.runtime_data
    await async_pre_backup(hass)
    pause = _process_data(hass).backup_pause
    assert pause is not None

    async def fail_resume(_handle: RuntimePauseHandle) -> None:
        raise StorageClosedError

    monkeypatch.setattr(manager, "async_resume", fail_resume)
    with pytest.raises(StorageClosedError):
        await async_post_backup(hass)

    assert _process_data(hass).backup_pause is pause
    monkeypatch.undo()
    await async_post_backup(hass)
    assert _process_data(hass).backup_pause is None
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_failed_pre_rolls_back_pause_and_allows_retry(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = await _setup_seeded_entry(hass, tmp_path / "backup-pre-retry.db")
    manager = entry.runtime_data
    original_close = manager.store.async_close
    close_calls = 0

    async def fail_first_close() -> None:
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            raise StorageClosedError
        await original_close()

    monkeypatch.setattr(manager.store, "async_close", fail_first_close)

    with pytest.raises(StorageClosedError):
        await async_pre_backup(hass)

    assert _process_data(hass).backup_pause is None
    assert manager.listener_entity_ids == ("person.fixture",)

    await async_pre_backup(hass)

    assert close_calls == 2
    assert _process_data(hass).backup_pause is not None
    assert manager.listener_entity_ids == ()
    await async_post_backup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
