from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

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
from custom_components.geofence_journal.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import Meters, Seconds
from custom_components.geofence_journal.settings import Settings
from homeassistant.components.diagnostics import REDACTED
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal import GeofenceJournalConfigEntry
    from homeassistant.core import HomeAssistant


def _entry_data(path: Path) -> dict[str, bool | float | int | str]:
    return {
        CONF_STORE_COORDINATES: False,
        CONF_ENTER_CONFIRMATION_SECONDS: 120,
        CONF_EXIT_CONFIRMATION_SECONDS: 180,
        CONF_COOLDOWN_SECONDS: 300,
        CONF_EXIT_MARGIN_METERS: 50.0,
        CONF_DATABASE_PATH: str(path),
    }


async def test_diagnostics_are_json_safe_and_exclude_location_identifiers(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    path = tmp_path / "private-location-name.db"
    manager = GeofenceJournalManager(
        hass,
        Settings(
            store_coordinates=False,
            enter_confirmation_seconds=Seconds(120),
            exit_confirmation_seconds=Seconds(180),
            cooldown_seconds=Seconds(300),
            exit_margin_meters=Meters(50),
            database_path=str(path),
        ),
    )
    await manager.async_start()
    entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data=_entry_data(path))
    entry.runtime_data = manager

    diagnostics = await async_get_config_entry_diagnostics(
        hass, cast("GeofenceJournalConfigEntry", entry)
    )
    serialized = json.dumps(diagnostics)
    await manager.async_stop()

    assert diagnostics["entry_data"][CONF_DATABASE_PATH] == REDACTED
    assert diagnostics["runtime"] == {
        "health": "healthy",
        "listener_entity_count": 0,
    }
    assert diagnostics["storage"]["available"] is True
    assert diagnostics["storage"]["schema_version"] == 1
    assert diagnostics["storage"]["event_count"] == 0
    assert str(path) not in serialized
    assert "private-location-name" not in serialized
    assert "latitude" not in serialized
    assert "longitude" not in serialized


async def test_diagnostics_degrade_safely_when_storage_is_closed(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    path = tmp_path / "closed-private.db"
    manager = GeofenceJournalManager(
        hass,
        Settings(
            store_coordinates=False,
            enter_confirmation_seconds=Seconds(0),
            exit_confirmation_seconds=Seconds(0),
            cooldown_seconds=Seconds(0),
            exit_margin_meters=Meters(0),
            database_path=str(path),
        ),
    )
    entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data=_entry_data(path))
    entry.runtime_data = manager

    diagnostics = await async_get_config_entry_diagnostics(
        hass, cast("GeofenceJournalConfigEntry", entry)
    )

    assert diagnostics["runtime"]["health"] == "unloaded"
    assert diagnostics["storage"] == {
        "available": False,
        "error_type": "StorageClosedError",
    }
    assert str(path) not in json.dumps(diagnostics)
