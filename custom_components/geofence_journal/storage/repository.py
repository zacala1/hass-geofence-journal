"""Synchronous, serialized SQLite repository."""

from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING, Final, Self, final

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path
    from types import TracebackType

    from custom_components.geofence_journal.models import (
        CoordinatePlace,
        JournalDefinition,
        RuleDefinition,
        TrackerDefinition,
        ZonePlace,
    )

from .db_types import SQLConnection, required_integer, required_text
from .errors import (
    DatabaseSchemaError,
    InjectedStorageFaultError,
    StorageClosedError,
    UnsupportedSchemaVersionError,
)
from .records import (
    ConfirmedTransition,
    RuntimeStateRecord,
    StorageDiagnostics,
    TransitionResult,
)
from .resources import upsert_journal, upsert_place, upsert_rule, upsert_tracker
from .runtime_state import delete_runtime_state, load_runtime_state, save_runtime_state
from .schema import SCHEMA_VERSION, bootstrap_v1, read_schema_version
from .transitions import confirm_transition, event_count

DEFAULT_BUSY_TIMEOUT_MS: Final = 1_000
MAX_BUSY_TIMEOUT_MS: Final = 10_000


@final
class SQLiteStore:
    """Own one SQLite connection and serialize all blocking operations."""

    def __init__(
        self, path: Path, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
    ) -> None:
        """Configure a bounded-timeout file repository."""
        if not 0 < busy_timeout_ms <= MAX_BUSY_TIMEOUT_MS:
            raise DatabaseSchemaError(
                detail="busy timeout must be between 1 and 10000 ms"
            )
        self._path = path
        self._busy_timeout_ms = busy_timeout_ms
        self._connection: SQLConnection | None = None
        self._lock = threading.RLock()

    def __enter__(self) -> Self:
        """Open the repository."""
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the repository."""
        self.close()

    def open(self, *, inject_migration_failure: bool = False) -> Self:
        """Open, validate, and idempotently bootstrap the database."""
        with self._lock:
            if self._connection is not None:
                return self
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self._path,
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
                check_same_thread=False,
            )
            try:
                version = _validated_version(connection)
                if version is None:
                    bootstrap_v1(connection, inject_failure=inject_migration_failure)
                self._configure_connection(connection)
            except (
                DatabaseSchemaError,
                InjectedStorageFaultError,
                sqlite3.Error,
                UnsupportedSchemaVersionError,
            ):
                connection.close()
                raise
            self._connection = connection
            return self

    def close(self) -> None:
        """Close after all earlier serialized work finishes."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def diagnostics(self) -> StorageDiagnostics:
        """Return active connection and schema invariants."""
        with self._lock:
            connection = self._require_connection()
            version_row = connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone()
            journal_row = connection.execute("PRAGMA journal_mode").fetchone()
            foreign_keys_row = connection.execute("PRAGMA foreign_keys").fetchone()
            timeout_row = connection.execute("PRAGMA busy_timeout").fetchone()
            if version_row is None:
                raise DatabaseSchemaError(detail="missing schema version row")
            if journal_row is None:
                raise DatabaseSchemaError(detail="missing journal mode row")
            if foreign_keys_row is None:
                raise DatabaseSchemaError(detail="missing foreign keys row")
            if timeout_row is None:
                raise DatabaseSchemaError(detail="missing busy timeout row")
            version = required_integer(version_row[0], field="schema version")
            journal_mode = required_text(journal_row[0], field="journal mode")
            foreign_keys = required_integer(foreign_keys_row[0], field="foreign keys")
            busy_timeout = required_integer(timeout_row[0], field="busy timeout")
            return StorageDiagnostics(
                schema_version=version,
                journal_mode=journal_mode,
                foreign_keys_enabled=foreign_keys == 1,
                busy_timeout_ms=busy_timeout,
            )

    def schema_objects(self, kind: str) -> set[str]:
        """List named schema objects of one SQLite kind."""
        with self._lock:
            rows = (
                self._require_connection()
                .execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))
                .fetchall()
            )
            return {
                required_text(row[0], field="sqlite_master.name")
                for row in rows
                if not required_text(row[0], field="sqlite_master.name").startswith(
                    "sqlite_"
                )
            }

    def schema_sql(self) -> tuple[str, ...]:
        """Return canonical persisted DDL for idempotency checks."""
        with self._lock:
            rows = (
                self._require_connection()
                .execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
                )
                .fetchall()
            )
            return tuple(
                required_text(row[0], field="sqlite_master.sql") for row in rows
            )

    def upsert_tracker(self, tracker: TrackerDefinition, timestamp: datetime) -> None:
        """Persist a tracker definition."""
        with self._lock:
            upsert_tracker(self._require_connection(), tracker, timestamp)

    def upsert_place(
        self, place: CoordinatePlace | ZonePlace, timestamp: datetime
    ) -> None:
        """Persist a place definition."""
        with self._lock:
            upsert_place(self._require_connection(), place, timestamp)

    def upsert_journal(self, journal: JournalDefinition, timestamp: datetime) -> None:
        """Persist a journal definition."""
        with self._lock:
            upsert_journal(self._require_connection(), journal, timestamp)

    def upsert_rule(self, rule: RuleDefinition, timestamp: datetime) -> None:
        """Persist a linked recording rule."""
        with self._lock:
            upsert_rule(self._require_connection(), rule, timestamp)

    def confirm_transition(
        self,
        transition: ConfirmedTransition,
        *,
        fail_after_event_insert: bool = False,
    ) -> TransitionResult:
        """Atomically append an idempotent event and replace runtime state."""
        with self._lock:
            return confirm_transition(
                self._require_connection(),
                transition,
                fail_after_event_insert=fail_after_event_insert,
            )

    def event_count(self) -> int:
        """Return the number of persisted location events."""
        with self._lock:
            return event_count(self._require_connection())

    def runtime_state(self, rule_id: str) -> RuntimeStateRecord | None:
        """Load the persisted runtime state for a rule."""
        with self._lock:
            return load_runtime_state(self._require_connection(), rule_id)

    def save_runtime_state(self, state: RuntimeStateRecord) -> None:
        """Persist all fields required for deterministic recovery."""
        with self._lock:
            save_runtime_state(self._require_connection(), state)

    def delete_runtime_state(self, rule_id: str) -> None:
        """Delete a runtime row without synthesizing an event."""
        with self._lock:
            delete_runtime_state(self._require_connection(), rule_id)

    def _configure_connection(self, connection: SQLConnection) -> None:
        _ = connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        _ = connection.execute("PRAGMA foreign_keys=ON")
        journal_row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        if journal_row is None:
            raise DatabaseSchemaError(detail="missing journal mode result")
        journal_mode = required_text(journal_row[0], field="journal mode")
        if journal_mode.lower() != "wal":
            raise DatabaseSchemaError(detail=f"WAL unavailable: {journal_mode}")
        foreign_keys_row = connection.execute("PRAGMA foreign_keys").fetchone()
        if (
            foreign_keys_row is None
            or required_integer(foreign_keys_row[0], field="foreign keys") != 1
        ):
            raise DatabaseSchemaError(detail="foreign key enforcement unavailable")

    def _require_connection(self) -> SQLConnection:
        if self._connection is None:
            raise StorageClosedError
        return self._connection


def _validated_version(connection: SQLConnection) -> int | None:
    version = read_schema_version(connection)
    if version is not None and version > SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(found=version, supported=SCHEMA_VERSION)
    if version is not None and version < SCHEMA_VERSION:
        raise DatabaseSchemaError(detail=f"unsupported old schema {version}")
    return version
