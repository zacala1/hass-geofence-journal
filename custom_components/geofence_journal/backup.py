"""Home Assistant backup lifecycle hooks for the integration-owned database."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, cast, final, override

import anyio
from homeassistant.exceptions import HomeAssistantError
from pydantic import ValidationError

from .const import DOMAIN
from .lifecycle import attach_secondary_failure
from .process_data import IntegrationProcessData
from .storage.errors import StorageError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import GeofenceJournalConfigEntry
    from .manager import GeofenceJournalManager

BACKUP_PAUSE_REASON = "home-assistant-backup"


@final
class BackupProcessDataError(HomeAssistantError):
    """The integration-wide backup state is unavailable or malformed."""

    @override
    def __str__(self) -> str:
        """Render the internal state invariant."""
        return "invalid Geofence Journal backup process data"


async def async_pre_backup(hass: HomeAssistant) -> None:
    """Pause observations, drain writes, and close SQLite before archiving."""
    manager = _loaded_manager(hass)
    if manager is None:
        return
    process_data = _require_process_data(hass)
    if process_data.backup_pause is not None:
        return
    handle = await manager.async_pause(BACKUP_PAUSE_REASON)
    try:
        await manager.store.async_close()
    except BaseException as primary_failure:
        try:
            with anyio.CancelScope(shield=True):
                await manager.async_resume(handle)
        except (OSError, sqlite3.Error, StorageError, RuntimeError) as failure:
            hass.data[DOMAIN] = process_data.model_copy(update={"backup_pause": handle})
            attach_secondary_failure(
                primary_failure,
                failure,
                operation="backup pause rollback",
            )
        raise
    hass.data[DOMAIN] = process_data.model_copy(update={"backup_pause": handle})


async def async_post_backup(hass: HomeAssistant) -> None:
    """Reopen SQLite and resume the runtime after backup completion."""
    process_data = _process_data(hass)
    if process_data is None or process_data.backup_pause is None:
        return
    manager = _loaded_manager(hass)
    if manager is None:
        hass.data[DOMAIN] = process_data.model_copy(update={"backup_pause": None})
        return
    await manager.store.async_open()
    await manager.async_resume(process_data.backup_pause)
    hass.data[DOMAIN] = process_data.model_copy(update={"backup_pause": None})


def _loaded_manager(hass: HomeAssistant) -> GeofenceJournalManager | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        return None
    entry = cast("GeofenceJournalConfigEntry", entries[0])
    return entry.runtime_data


def _require_process_data(hass: HomeAssistant) -> IntegrationProcessData:
    process_data = _process_data(hass)
    if process_data is None:
        raise BackupProcessDataError
    return process_data


def _process_data(hass: HomeAssistant) -> IntegrationProcessData | None:
    raw = hass.data.get(DOMAIN)
    if raw is None:
        return None
    try:
        return IntegrationProcessData.model_validate(raw)
    except ValidationError as error:
        raise BackupProcessDataError from error
