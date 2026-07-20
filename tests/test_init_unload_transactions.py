from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from custom_components.geofence_journal import (
    GeofenceJournalConfigEntry,
    async_unload_entry,
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
from custom_components.geofence_journal.entity_state import UnloadedEntityState
from custom_components.geofence_journal.storage.db_types import required_integer
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import SQLConnection
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
            CONF_DATABASE_PATH: str(path),
        },
    )


def _select_one(connection: SQLConnection) -> int:
    row = connection.execute("SELECT 1").fetchone()
    assert row is not None
    return required_integer(row[0], field="select one")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_platform_unload_failure_leaves_manager_and_services_running(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(tmp_path / "failed-unload.db")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    typed_entry = cast("GeofenceJournalConfigEntry", entry)
    manager = typed_entry.runtime_data

    async def reject_platform_unload(
        _entry: GeofenceJournalConfigEntry, _platforms: Iterable[Platform]
    ) -> bool:
        return False

    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", reject_platform_unload
    )

    assert not await async_unload_entry(hass, typed_entry)
    assert await manager.store.async_run_operation(_select_one) == 1
    assert hass.services.has_service(DOMAIN, "upsert_tracker")
    assert not isinstance(manager.entity_state, UnloadedEntityState)

    monkeypatch.undo()
    assert await hass.config_entries.async_unload(entry.entry_id)
