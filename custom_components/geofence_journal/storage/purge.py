"""Index-friendly, auditable deletion of historical journal events."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final, override

from .db_types import SQLConnection, required_integer
from .errors import DatabaseSchemaError
from .records import utc_text

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.geofence_journal.models import JournalId


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


@final
class PurgeConfirmationError(Exception):
    """A mutating purge was requested without explicit confirmation."""

    @override
    def __str__(self) -> str:
        """Render the confirmation requirement."""
        return "permanent event purge requires confirm=True"


@dataclass(frozen=True, slots=True)
class _PurgeStatements:
    """Static SQL bundle for one indexed event selection shape."""

    event_count: str
    revision_count: str
    delete_revisions: str
    clear_runtime_event: str
    clear_original_event: str
    delete_events: str


_ALL_JOURNALS: Final = _PurgeStatements(
    event_count="""SELECT COUNT(*) FROM location_events
WHERE occurred_at < ?""",
    revision_count="""SELECT COUNT(*) FROM event_revisions
WHERE event_id IN (SELECT id FROM location_events WHERE occurred_at < ?)""",
    delete_revisions="""DELETE FROM event_revisions WHERE event_id IN
(SELECT id FROM location_events WHERE occurred_at < ?)""",
    clear_runtime_event="""UPDATE runtime_states
SET last_event_id=NULL,last_event_type=NULL,last_event_at=NULL
WHERE last_event_id IN
(SELECT id FROM location_events WHERE occurred_at < ?)""",
    clear_original_event="""UPDATE location_events
SET original_event_id=NULL WHERE original_event_id IN
(SELECT id FROM location_events WHERE occurred_at < ?)""",
    delete_events="""DELETE FROM location_events WHERE occurred_at < ?""",
)
_ONE_JOURNAL: Final = _PurgeStatements(
    event_count="""SELECT COUNT(*) FROM location_events
WHERE journal_id = ? AND occurred_at < ?""",
    revision_count="""SELECT COUNT(*) FROM event_revisions
WHERE event_id IN (SELECT id FROM location_events
WHERE journal_id = ? AND occurred_at < ?)""",
    delete_revisions="""DELETE FROM event_revisions WHERE event_id IN
(SELECT id FROM location_events
WHERE journal_id = ? AND occurred_at < ?)""",
    clear_runtime_event="""UPDATE runtime_states
SET last_event_id=NULL,last_event_type=NULL,last_event_at=NULL
WHERE last_event_id IN (SELECT id FROM location_events
WHERE journal_id = ? AND occurred_at < ?)""",
    clear_original_event="""UPDATE location_events
SET original_event_id=NULL WHERE original_event_id IN
(SELECT id FROM location_events
WHERE journal_id = ? AND occurred_at < ?)""",
    delete_events="""DELETE FROM location_events
WHERE journal_id = ? AND occurred_at < ?""",
)


def purge_events(connection: SQLConnection, request: PurgeRequest) -> PurgeResult:
    """Count or atomically delete events strictly before a UTC instant."""
    statements, parameters = _purge_selection(request)
    if request.dry_run:
        events, revisions = _purge_counts(connection, statements, parameters)
        return PurgeResult(events, revisions, 0, 0, dry_run=True)
    if not request.confirm:
        raise PurgeConfirmationError
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        events, revisions = _purge_counts(connection, statements, parameters)
        _ = connection.execute(statements.delete_revisions, parameters)
        _ = connection.execute(statements.clear_runtime_event, parameters)
        _ = connection.execute(statements.clear_original_event, parameters)
        _ = connection.execute(statements.delete_events, parameters)
    except DatabaseSchemaError, sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()
    return PurgeResult(events, revisions, events, revisions, dry_run=False)


def _purge_selection(
    request: PurgeRequest,
) -> tuple[_PurgeStatements, tuple[str, ...]]:
    before = utc_text(request.before)
    if request.journal_id is None:
        return _ALL_JOURNALS, (before,)
    return _ONE_JOURNAL, (str(request.journal_id), before)


def _purge_counts(
    connection: SQLConnection,
    statements: _PurgeStatements,
    parameters: tuple[str, ...],
) -> tuple[int, int]:
    event_row = connection.execute(statements.event_count, parameters).fetchone()
    revision_row = connection.execute(statements.revision_count, parameters).fetchone()
    if event_row is None or revision_row is None:
        raise DatabaseSchemaError(detail="missing purge count result")
    return (
        required_integer(event_row[0], field="matched events"),
        required_integer(revision_row[0], field="matched revisions"),
    )
