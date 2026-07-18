"""Explicit, auditable SQLite data lifecycle primitives."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, final, override

from .db_types import SQLConnection, required_integer, required_text
from .errors import DatabaseSchemaError
from .records import utc_text
from .schema import SCHEMA_VERSION, read_schema_version

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from custom_components.geofence_journal.models import JournalId

RESET_CONFIRMATION_PHRASE: Final = "DELETE ALL GEOFENCE JOURNAL DATA"
_EVENT_COUNT_SQL: Final = """SELECT COUNT(*) FROM location_events
WHERE occurred_at < ? AND (? IS NULL OR journal_id = ?)"""
_REVISION_COUNT_SQL: Final = """SELECT COUNT(*) FROM event_revisions
WHERE event_id IN (SELECT id FROM location_events
WHERE occurred_at < ? AND (? IS NULL OR journal_id = ?))"""
_DELETE_REVISIONS_SQL: Final = """DELETE FROM event_revisions WHERE event_id IN
(SELECT id FROM location_events
WHERE occurred_at < ? AND (? IS NULL OR journal_id = ?))"""
_CLEAR_RUNTIME_EVENT_SQL: Final = """UPDATE runtime_states
SET last_event_id=NULL,last_event_type=NULL,last_event_at=NULL
WHERE last_event_id IN (SELECT id FROM location_events
WHERE occurred_at < ? AND (? IS NULL OR journal_id = ?))"""
_CLEAR_ORIGINAL_EVENT_SQL: Final = """UPDATE location_events
SET original_event_id=NULL WHERE original_event_id IN
(SELECT id FROM location_events
WHERE occurred_at < ? AND (? IS NULL OR journal_id = ?))"""
_DELETE_EVENTS_SQL: Final = """DELETE FROM location_events
WHERE occurred_at < ? AND (? IS NULL OR journal_id = ?)"""


class MaintenanceCoordinator(Protocol):
    """Runtime hook that pauses observations and drains accepted work."""

    def pause_and_drain(self) -> AbstractAsyncContextManager[None]:
        """Return a scope that resumes observation processing on exit."""
        ...


@dataclass(frozen=True, slots=True)
class PurgeRequest:
    """Selection and explicit-safety controls for permanent event deletion."""

    before: datetime
    journal_id: JournalId | None
    dry_run: bool
    confirm: bool


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """Counts selected and deleted by one purge invocation."""

    matched_events: int
    matched_revisions: int
    deleted_events: int
    deleted_revisions: int
    dry_run: bool


@dataclass(frozen=True, slots=True)
class CompactResult:
    """Observable checkpoint and database-size outcome."""

    database_bytes_before: int
    database_bytes_after: int
    wal_bytes_before: int
    wal_bytes_after: int
    checkpoint_log_pages: int
    checkpointed_pages: int
    checkpoint_busy: bool


@dataclass(frozen=True, slots=True)
class ResetRequest:
    """Exact destructive-action confirmation supplied by an administrator."""

    confirmation: str


@dataclass(frozen=True, slots=True)
class ResetResult:
    """Rows removed while preserving a freshly initialized v1 schema."""

    deleted_trackers: int
    deleted_places: int
    deleted_journals: int
    deleted_rules: int
    deleted_events: int
    deleted_revisions: int
    deleted_runtime_states: int
    schema_version: int

    @property
    def deleted_resources(self) -> int:
        """Return the combined configured-resource deletion count."""
        return (
            self.deleted_trackers
            + self.deleted_places
            + self.deleted_journals
            + self.deleted_rules
        )


@final
class PurgeConfirmationError(Exception):
    """A mutating purge was requested without explicit confirmation."""

    @override
    def __str__(self) -> str:
        """Render the confirmation requirement."""
        return "permanent event purge requires confirm=True"


@final
class ResetConfirmationError(Exception):
    """The exact reset confirmation phrase was not supplied."""

    @override
    def __str__(self) -> str:
        """Render the exact phrase requirement."""
        return f"reset requires exact phrase: {RESET_CONFIRMATION_PHRASE}"


@final
class CheckpointBusyError(Exception):
    """WAL readers prevented a complete bounded checkpoint."""

    @override
    def __str__(self) -> str:
        """Render the checkpoint failure."""
        return "WAL checkpoint remained busy; database was not vacuumed"


def purge_events(connection: SQLConnection, request: PurgeRequest) -> PurgeResult:
    """Count or atomically delete events strictly before a UTC instant."""
    before = utc_text(request.before)
    parameters = (before, request.journal_id, request.journal_id)
    if request.dry_run:
        events, revisions = _purge_counts(connection, parameters)
        return PurgeResult(events, revisions, 0, 0, dry_run=True)
    if not request.confirm:
        raise PurgeConfirmationError
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        events, revisions = _purge_counts(connection, parameters)
        _ = connection.execute(_DELETE_REVISIONS_SQL, parameters)
        _ = connection.execute(_CLEAR_RUNTIME_EVENT_SQL, parameters)
        _ = connection.execute(_CLEAR_ORIGINAL_EVENT_SQL, parameters)
        _ = connection.execute(_DELETE_EVENTS_SQL, parameters)
    except DatabaseSchemaError, sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()
    return PurgeResult(events, revisions, events, revisions, dry_run=False)


def compact_database(connection: SQLConnection) -> CompactResult:
    """Checkpoint WAL, VACUUM the main database, and report file sizes."""
    database_row = connection.execute("PRAGMA database_list").fetchone()
    if database_row is None:
        raise DatabaseSchemaError(detail="missing SQLite main database")
    database_path = Path(required_text(database_row[2], field="main database path"))
    wal_path = database_path.with_name(f"{database_path.name}-wal")
    database_before = _file_size(database_path)
    wal_before = _file_size(wal_path)
    checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if checkpoint is None:
        raise DatabaseSchemaError(detail="missing WAL checkpoint result")
    busy = required_integer(checkpoint[0], field="checkpoint busy")
    log_pages = required_integer(checkpoint[1], field="checkpoint log pages")
    checkpointed = required_integer(checkpoint[2], field="checkpointed pages")
    if busy != 0:
        raise CheckpointBusyError
    _ = connection.execute("VACUUM")
    final_checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if final_checkpoint is None:
        raise DatabaseSchemaError(detail="missing post-VACUUM checkpoint result")
    if required_integer(final_checkpoint[0], field="post-VACUUM busy") != 0:
        raise CheckpointBusyError
    return CompactResult(
        database_bytes_before=database_before,
        database_bytes_after=_file_size(database_path),
        wal_bytes_before=wal_before,
        wal_bytes_after=_file_size(wal_path),
        checkpoint_log_pages=log_pages,
        checkpointed_pages=checkpointed,
        checkpoint_busy=False,
    )


def reset_database(connection: SQLConnection, request: ResetRequest) -> ResetResult:
    """Atomically clear domain data and reinitialize the v1 version row."""
    if request.confirmation != RESET_CONFIRMATION_PHRASE:
        raise ResetConfirmationError
    version = read_schema_version(connection)
    if version != SCHEMA_VERSION:
        raise DatabaseSchemaError(detail=f"reset requires schema v{SCHEMA_VERSION}")
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        counts = _reset_counts(connection)
        for statement in (
            "DELETE FROM event_revisions",
            "DELETE FROM runtime_states",
            "DELETE FROM location_events",
            "DELETE FROM recording_rules",
            "DELETE FROM journals",
            "DELETE FROM places",
            "DELETE FROM trackers",
            "DELETE FROM schema_version",
        ):
            _ = connection.execute(statement)
        _ = connection.execute(
            "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
        )
    except DatabaseSchemaError, sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()
    return ResetResult(*counts, schema_version=SCHEMA_VERSION)


def _purge_counts(
    connection: SQLConnection,
    parameters: tuple[str, JournalId | None, JournalId | None],
) -> tuple[int, int]:
    event_row = connection.execute(_EVENT_COUNT_SQL, parameters).fetchone()
    revision_row = connection.execute(_REVISION_COUNT_SQL, parameters).fetchone()
    if event_row is None or revision_row is None:
        raise DatabaseSchemaError(detail="missing purge count result")
    return (
        required_integer(event_row[0], field="matched events"),
        required_integer(revision_row[0], field="matched revisions"),
    )


def _reset_counts(
    connection: SQLConnection,
) -> tuple[int, int, int, int, int, int, int]:
    row = connection.execute(
        """SELECT (SELECT COUNT(*) FROM trackers),(SELECT COUNT(*) FROM places),
        (SELECT COUNT(*) FROM journals),(SELECT COUNT(*) FROM recording_rules),
        (SELECT COUNT(*) FROM location_events),(SELECT COUNT(*) FROM event_revisions),
        (SELECT COUNT(*) FROM runtime_states)"""
    ).fetchone()
    if row is None:
        raise DatabaseSchemaError(detail="missing reset count result")
    return (
        required_integer(row[0], field="tracker count"),
        required_integer(row[1], field="place count"),
        required_integer(row[2], field="journal count"),
        required_integer(row[3], field="rule count"),
        required_integer(row[4], field="event count"),
        required_integer(row[5], field="revision count"),
        required_integer(row[6], field="runtime count"),
    )


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0
