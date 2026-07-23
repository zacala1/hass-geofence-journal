from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from custom_components.geofence_journal.const import DOMAIN
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import Meters, Seconds
from custom_components.geofence_journal.repairs import (
    DATABASE_ISSUE_ID,
    async_subscribe_database_issue,
)
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage.errors import StorageClosedError
from homeassistant.helpers import issue_registry as ir

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

NOW: Final = datetime(2026, 7, 23, 12, tzinfo=UTC)


async def test_database_repair_issue_tracks_runtime_health_without_private_data(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    manager = GeofenceJournalManager(
        hass,
        Settings(
            store_coordinates=False,
            enter_confirmation_seconds=Seconds(0),
            exit_confirmation_seconds=Seconds(0),
            cooldown_seconds=Seconds(0),
            exit_margin_meters=Meters(0),
            database_path=str(tmp_path / "private-database-name.db"),
        ),
    )
    unsubscribe = async_subscribe_database_issue(hass, manager)
    await manager.async_start()
    await manager.store.async_close()

    with pytest.raises(StorageClosedError):
        await manager.async_refresh_resources()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, DATABASE_ISSUE_ID)

    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_key == "database_unavailable"
    assert issue.translation_placeholders is None
    assert issue.data is None

    manager.record_event(NOW)

    assert ir.async_get(hass).async_get_issue(DOMAIN, DATABASE_ISSUE_ID) is None
    unsubscribe()
    await manager.async_stop()
