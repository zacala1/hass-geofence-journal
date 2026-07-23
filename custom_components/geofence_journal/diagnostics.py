"""Privacy-safe Home Assistant diagnostics platform."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypedDict, assert_never, cast

from anyio.to_thread import run_sync
from homeassistant.components import diagnostics as ha_diagnostics
from pydantic import ConfigDict, TypeAdapter

from .const import CONF_DATABASE_PATH
from .entity_state import (
    DatabaseErrorEntityState,
    GeofenceJournalEntityState,
    HealthyEntityState,
    UnloadedEntityState,
)
from .storage.diagnostics import (
    StorageDiagnosticSnapshot,
    collect_storage_diagnostics,
)
from .storage.errors import StorageError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import GeofenceJournalConfigEntry

type DiagnosticScalar = bool | int | float | str | None
type HealthValue = Literal["healthy", "database_error", "unloaded"]

ENTRY_DATA_ADAPTER: Final[TypeAdapter[dict[str, DiagnosticScalar]]] = TypeAdapter(
    dict[str, DiagnosticScalar], config=ConfigDict(strict=True)
)
TO_REDACT: Final = (CONF_DATABASE_PATH,)


class RuntimeDiagnosticData(TypedDict):
    """Identifier-free runtime health fields."""

    health: HealthValue
    listener_entity_count: int


class AvailableStorageDiagnosticData(TypedDict):
    """Healthy storage details."""

    available: Literal[True]
    schema_version: int
    journal_mode: str
    foreign_keys_enabled: bool
    quick_check_ok: bool
    tracker_count: int
    place_count: int
    journal_count: int
    rule_count: int
    active_rule_count: int
    event_count: int
    revision_count: int
    runtime_state_count: int
    database_bytes: int
    wal_bytes: int


class UnavailableStorageDiagnosticData(TypedDict):
    """Storage failure details that never include exception messages."""

    available: Literal[False]
    error_type: str


type StorageDiagnosticData = (
    AvailableStorageDiagnosticData | UnavailableStorageDiagnosticData
)


class ConfigEntryDiagnosticData(TypedDict):
    """Complete Home Assistant config-entry diagnostic response."""

    entry_data: dict[str, DiagnosticScalar]
    runtime: RuntimeDiagnosticData
    storage: StorageDiagnosticData


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant,
    entry: GeofenceJournalConfigEntry,
) -> ConfigEntryDiagnosticData:
    """Return redacted configuration and identifier-free runtime diagnostics."""
    manager = entry.runtime_data
    redacted_raw = cast(
        "object", ha_diagnostics.async_redact_data(entry.data, TO_REDACT)
    )
    redacted = ENTRY_DATA_ADAPTER.validate_python(redacted_raw)
    runtime = RuntimeDiagnosticData(
        health=_health_value(manager.entity_state),
        listener_entity_count=len(manager.listener_entity_ids),
    )
    try:
        snapshot = await manager.store.async_run_read_operation(
            collect_storage_diagnostics
        )
        database_bytes, wal_bytes = await run_sync(
            _file_sizes, Path(manager.settings.database_path)
        )
    except (OSError, sqlite3.Error, StorageError) as error:
        storage: StorageDiagnosticData = UnavailableStorageDiagnosticData(
            available=False,
            error_type=type(error).__name__,
        )
    else:
        storage = _available_storage(snapshot, database_bytes, wal_bytes)
    return ConfigEntryDiagnosticData(
        entry_data=redacted,
        runtime=runtime,
        storage=storage,
    )


def _health_value(state: GeofenceJournalEntityState) -> HealthValue:
    match state:
        case HealthyEntityState():
            return "healthy"
        case DatabaseErrorEntityState():
            return "database_error"
        case UnloadedEntityState():
            return "unloaded"
        case unreachable:
            assert_never(unreachable)


def _available_storage(
    snapshot: StorageDiagnosticSnapshot,
    database_bytes: int,
    wal_bytes: int,
) -> AvailableStorageDiagnosticData:
    return AvailableStorageDiagnosticData(
        available=True,
        schema_version=snapshot.schema_version,
        journal_mode=snapshot.journal_mode,
        foreign_keys_enabled=snapshot.foreign_keys_enabled,
        quick_check_ok=snapshot.quick_check_ok,
        tracker_count=snapshot.tracker_count,
        place_count=snapshot.place_count,
        journal_count=snapshot.journal_count,
        rule_count=snapshot.rule_count,
        active_rule_count=snapshot.active_rule_count,
        event_count=snapshot.event_count,
        revision_count=snapshot.revision_count,
        runtime_state_count=snapshot.runtime_state_count,
        database_bytes=database_bytes,
        wal_bytes=wal_bytes,
    )


def _file_sizes(database_path: Path) -> tuple[int, int]:
    wal_path = Path(f"{database_path}-wal")
    return _file_size(database_path), _file_size(wal_path)


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0
