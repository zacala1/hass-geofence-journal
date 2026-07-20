"""Home Assistant config-entry lifecycle for Geofence Journal."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from pydantic import ConfigDict, TypeAdapter, ValidationError

from .const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_STORE_COORDINATES,
    DOMAIN,
)
from .export import ExportArtifact, ExportRegistry, export_directory
from .http import (
    async_cleanup_orphaned_exports,
    async_register_export_view,
    async_schedule_export_cleanup,
)
from .management_backend import (
    ManagementBackendDependencies,
    SQLiteManagementBackend,
)
from .manager import GeofenceJournalManager
from .process_data import IntegrationProcessData
from .services import async_register_services, async_unregister_services
from .settings import ConfigValue, Settings, SettingsFieldError
from .storage.errors import (
    DatabaseSchemaError,
    InjectedStorageFaultError,
    UnsupportedSchemaVersionError,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .models import Clock

type GeofenceJournalConfigEntry = ConfigEntry[GeofenceJournalManager]

PLATFORMS: Final = (Platform.SENSOR, Platform.BINARY_SENSOR)
SETTING_FIELDS: Final = (
    CONF_STORE_COORDINATES,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_COOLDOWN_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_DATABASE_PATH,
)
RETRYABLE_SQLITE_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_READONLY,
    }
)
CONFIG_VALUE_ADAPTER: Final[TypeAdapter[ConfigValue]] = TypeAdapter(
    ConfigValue, config=ConfigDict(strict=True)
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GeofenceJournalConfigEntry
) -> bool:
    """Set up storage, recovered runtime, listeners, and fixed platforms."""
    try:
        settings = _entry_settings(hass, entry)
    except SettingsFieldError as error:
        raise ConfigEntryError(str(error)) from error
    manager = GeofenceJournalManager(hass, settings)
    try:
        await manager.async_start()
    except OSError as error:
        raise ConfigEntryNotReady(str(error)) from error
    except sqlite3.OperationalError as error:
        if _retryable_sqlite(error):
            raise ConfigEntryNotReady(str(error)) from error
        raise ConfigEntryError(str(error)) from error
    except (
        DatabaseSchemaError,
        InjectedStorageFaultError,
        UnsupportedSchemaVersionError,
        sqlite3.DatabaseError,
    ) as error:
        raise ConfigEntryError(str(error)) from error
    entry.runtime_data = manager
    services_registered = False
    platforms_loaded = False
    setup_complete = False
    try:
        exports = await _async_export_registry(hass, manager.clock)
        backend = SQLiteManagementBackend(
            manager.store,
            ManagementBackendDependencies(
                exports=exports,
                coordinator=manager,
                clock=manager.clock,
                settings=settings,
                schedule_export_cleanup=lambda artifact: _schedule_export_cleanup(
                    hass, manager, exports, artifact
                ),
                on_event=manager.record_event,
            ),
        )
        services_registered = True
        await async_register_services(hass, backend)
        platforms_loaded = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        setup_complete = True
    finally:
        if not setup_complete:
            await manager.async_stop()
            if platforms_loaded:
                _ = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            if services_registered:
                await async_unregister_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GeofenceJournalConfigEntry
) -> bool:
    """Stop observations and storage, then remove platforms and services."""
    await entry.runtime_data.async_stop()
    platforms_unloaded = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if platforms_unloaded:
        await async_unregister_services(hass)
    return platforms_unloaded


async def _async_reload_entry(
    hass: HomeAssistant, entry: GeofenceJournalConfigEntry
) -> None:
    """Reload settings and replace every runtime callback generation."""
    _ = await hass.config_entries.async_reload(entry.entry_id)


def _entry_settings(hass: HomeAssistant, entry: GeofenceJournalConfigEntry) -> Settings:
    raw: dict[str, ConfigValue] = {}
    for field in SETTING_FIELDS:
        try:
            raw[field] = CONFIG_VALUE_ADAPTER.validate_python(entry.data.get(field))
        except ValidationError as error:
            raise SettingsFieldError(field=field) from error
    settings = Settings.from_mapping(raw)
    configured_path = Path(settings.database_path)
    if configured_path.is_absolute():
        return settings
    return replace(settings, database_path=hass.config.path(settings.database_path))


async def _async_export_registry(hass: HomeAssistant, clock: Clock) -> ExportRegistry:
    if DOMAIN not in hass.data:
        exports = ExportRegistry(export_directory(hass), clock)
        _ = await async_cleanup_orphaned_exports(hass, exports)
        async_register_export_view(hass, exports)
        hass.data[DOMAIN] = IntegrationProcessData(exports=exports)
        return exports
    try:
        process_data = IntegrationProcessData.model_validate(hass.data[DOMAIN])
    except ValidationError as error:
        detail = "invalid Geofence Journal process data"
        raise ConfigEntryError(detail) from error
    return process_data.exports


def _schedule_export_cleanup(
    hass: HomeAssistant,
    _manager: GeofenceJournalManager,
    exports: ExportRegistry,
    artifact: ExportArtifact,
) -> None:
    _ = async_schedule_export_cleanup(hass, exports, artifact)


def _retryable_sqlite(error: sqlite3.OperationalError) -> bool:
    """Identify only temporary lock/path/read-only SQLite failures."""
    error_code = getattr(error, "sqlite_errorcode", None)
    return error_code is not None and error_code & 0xFF in RETRYABLE_SQLITE_CODES
