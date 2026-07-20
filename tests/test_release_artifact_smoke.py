from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from custom_components import geofence_journal
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
from custom_components.geofence_journal.storage.db_types import (
    SQLConnection,
    required_integer,
    required_text,
)
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from custom_components.geofence_journal import GeofenceJournalConfigEntry
    from homeassistant.core import HomeAssistant


def _database_contract(connection: SQLConnection) -> tuple[str, int, int]:
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    schema_version = connection.execute("SELECT version FROM schema_version").fetchone()
    assert journal_mode is not None
    assert foreign_keys is not None
    assert schema_version is not None
    return (
        required_text(journal_mode[0], field="journal mode"),
        required_integer(foreign_keys[0], field="foreign keys"),
        required_integer(schema_version[0], field="schema version"),
    )


def _assert_artifact_origin() -> None:
    expected_root = os.environ.get("EXPECTED_INTEGRATION_ROOT")
    if expected_root is None:
        return
    package = Path(geofence_journal.__file__).resolve().parent
    expected = (
        Path(expected_root) / "custom_components" / "geofence_journal"
    ).resolve()
    assert package == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_release_artifact_setup_reload_sqlite_and_unload(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _assert_artifact_origin()

    database_path = tmp_path / "artifact-smoke.db"
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        data={
            CONF_STORE_COORDINATES: False,
            CONF_ENTER_CONFIRMATION_SECONDS: 0,
            CONF_EXIT_CONFIRMATION_SECONDS: 0,
            CONF_COOLDOWN_SECONDS: 0,
            CONF_EXIT_MARGIN_METERS: 50.0,
            CONF_DATABASE_PATH: str(database_path),
        },
    )
    mock_entry.add_to_hass(hass)
    entry = cast("GeofenceJournalConfigEntry", mock_entry)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await entry.runtime_data.store.async_run_operation(_database_contract) == (
        "wal",
        1,
        1,
    )
    first_runtime = entry.runtime_data

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first_runtime
    assert await entry.runtime_data.store.async_run_operation(_database_contract) == (
        "wal",
        1,
        1,
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
