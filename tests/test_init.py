from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal import async_setup_entry
from custom_components.geofence_journal.const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_MAX_GPS_ACCURACY_METERS,
    CONF_STORE_COORDINATES,
    DOMAIN,
    TITLE,
)
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.errors import (
    UnsupportedSchemaVersionError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from custom_components.geofence_journal import GeofenceJournalConfigEntry
    from homeassistant.const import Platform
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
            CONF_MAX_GPS_ACCURACY_METERS: 100.0,
            CONF_DATABASE_PATH: str(path),
        },
    )


def _unlink_if_present(path: Path) -> None:
    path.unlink(missing_ok=True)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_and_unload_own_typed_runtime_platforms_and_services(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: a valid sole config entry.
    entry = _entry(tmp_path / "lifecycle.db")
    entry.add_to_hass(hass)

    # When: Home Assistant sets up the integration.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Then: one typed runtime owns the fixed platforms and service surface.
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.geofence_journal_last_event") is not None
    assert hass.states.get("binary_sensor.geofence_journal_healthy") is not None
    assert hass.services.has_service(DOMAIN, "upsert_tracker")

    # When: the sole entry unloads.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    # Then: platforms and the once-registered services are gone.
    last_event = hass.states.get("sensor.geofence_journal_last_event")
    healthy = hass.states.get("binary_sensor.geofence_journal_healthy")
    assert last_event is not None
    assert last_event.state == STATE_UNAVAILABLE
    assert healthy is not None
    assert healthy.state == STATE_UNAVAILABLE
    assert not hass.services.has_service(DOMAIN, "upsert_tracker")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_entry_update_reloads_with_a_new_runtime(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: one loaded entry with its first runtime generation.
    entry = _entry(tmp_path / "reload.db")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    # When: an entry setting changes and the update listener reloads it.
    updated = dict(entry.data)
    updated[CONF_STORE_COORDINATES] = True
    _ = hass.config_entries.async_update_entry(entry, data=updated)
    await hass.async_block_till_done()

    # Then: the old callbacks are detached and one fresh runtime is loaded.
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.geofence_journal_last_event") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unopenable_path_is_retryable(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: the configured database path is an existing directory.
    database_path = tmp_path / "directory.db"
    database_path.mkdir()
    entry = _entry(database_path)

    # When / Then: setup classifies the temporary path failure as retryable.
    with pytest.raises(ConfigEntryNotReady):
        _ = await async_setup_entry(hass, entry)


async def test_corrupt_database_is_fatal_and_preserved(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: the configured path contains non-SQLite bytes.
    database_path = tmp_path / "corrupt.db"
    original = b"not a sqlite database"
    _ = database_path.write_bytes(original)
    entry = _entry(database_path)

    # When / Then: setup fails fatally and never recreates the file.
    with pytest.raises(ConfigEntryError):
        _ = await async_setup_entry(hass, entry)
    assert database_path.read_bytes() == original


async def test_future_schema_is_fatal_without_reset(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: a valid SQLite file declaring a future schema version.
    database_path = tmp_path / "future.db"
    with closing(sqlite3.connect(database_path)) as connection:
        _ = connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        _ = connection.execute("INSERT INTO schema_version(version) VALUES (2)")
        connection.commit()
    entry = _entry(database_path)

    # When / Then: setup is fatal and version 2 remains authoritative.
    with pytest.raises(ConfigEntryError):
        _ = await async_setup_entry(hass, entry)
    with pytest.raises(UnsupportedSchemaVersionError), SQLiteStore(database_path):
        pass


async def test_malformed_schema_is_fatal_without_recreation(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: SQLite has the schema table name but not its required version column.
    database_path = tmp_path / "malformed.db"
    with closing(sqlite3.connect(database_path)) as connection:
        _ = connection.execute("CREATE TABLE schema_version(wrong INTEGER NOT NULL)")
        _ = connection.execute("INSERT INTO schema_version(wrong) VALUES (7)")
        connection.commit()
    entry = _entry(database_path)

    # When / Then: SQLITE_ERROR is fatal rather than an endless retry/reset loop.
    with pytest.raises(ConfigEntryError):
        _ = await async_setup_entry(hass, entry)
    with pytest.raises(sqlite3.OperationalError), SQLiteStore(database_path):
        pass


async def test_platform_setup_failure_rolls_back_services_and_storage(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: HTTP is ready and platform forwarding fails after service registration.
    assert await async_setup_component(hass, "http", {})
    database_path = tmp_path / "partial-setup.db"
    entry = _entry(database_path)

    async def fail_forward(
        _entry: GeofenceJournalConfigEntry, _platforms: Iterable[Platform]
    ) -> None:
        raise RuntimeError

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", fail_forward)

    # When: entry setup reaches the injected platform failure.
    with pytest.raises(RuntimeError):
        _ = await async_setup_entry(hass, entry)

    # Then: registered services are removed and the SQLite connection is closed.
    assert not hass.services.has_service(DOMAIN, "upsert_tracker")
    with SQLiteStore(database_path):
        pass


async def test_invalid_entry_data_is_a_fatal_configuration_error(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data={})

    with pytest.raises(ConfigEntryError):
        _ = await async_setup_entry(hass, entry)


async def test_open_os_error_is_retryable(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(tmp_path / "os-error.db")

    async def fail_start(_manager: GeofenceJournalManager) -> None:
        detail = "temporary path failure"
        raise OSError(detail)

    monkeypatch.setattr(GeofenceJournalManager, "async_start", fail_start)
    with pytest.raises(ConfigEntryNotReady, match="temporary path failure"):
        _ = await async_setup_entry(hass, entry)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_relative_database_path_resolves_below_the_ha_config_directory(
    hass: HomeAssistant,
) -> None:
    relative_path = Path("relative-coverage.db")
    resolved_path = Path(hass.config.path(str(relative_path)))
    entry = _entry(relative_path)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert await hass.async_add_executor_job(resolved_path.is_file)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_add_executor_job(_unlink_if_present, resolved_path)


async def test_invalid_process_registry_rolls_back_started_manager(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    hass.data[DOMAIN] = {}
    entry = _entry(tmp_path / "invalid-process-data.db")

    with pytest.raises(ConfigEntryError, match="invalid Geofence Journal process"):
        _ = await async_setup_entry(hass, entry)
    with SQLiteStore(tmp_path / "invalid-process-data.db"):
        pass
