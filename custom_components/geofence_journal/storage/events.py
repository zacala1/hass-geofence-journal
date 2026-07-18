"""Atomic persistence primitives for manual event lifecycle changes."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NewType, final, override
from uuid import uuid4

from custom_components.geofence_journal.models import (
    EventId,
    EventStatus,
    JournalId,
    PlaceId,
    TrackerId,
)

from .db_types import SQLConnection, required_integer, required_text
from .errors import DatabaseSchemaError
from .records import utc_text

if TYPE_CHECKING:
    from datetime import datetime

type ReferenceKind = Literal["journal", "tracker", "place"]
RevisionId = NewType("RevisionId", str)


@dataclass(frozen=True, slots=True)
class AddEventRequest:
    """Complete typed input for one manually-created event."""

    event_id: EventId
    journal_id: JournalId
    tracker_id: TrackerId
    place_id: PlaceId
    occurred_at: datetime
    confirmed_at: datetime
    latitude: float | None
    longitude: float | None
    accuracy_m: float | None
    note: str | None


@dataclass(frozen=True, slots=True)
class AddEventResult:
    """Identity of a committed manual event."""

    event_id: EventId


@dataclass(frozen=True, slots=True)
class EventMutation:
    """Audit context for an exclude or restore operation."""

    event_id: EventId
    changed_at: datetime
    changed_by: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class EventMutationResult:
    """Outcome of an idempotent event status mutation."""

    event_id: EventId
    revision_id: RevisionId | None
    status: EventStatus
    changed: bool


@final
class MissingEventReferenceError(Exception):
    """A manual event names a resource that is not present."""

    __slots__ = ("identifier", "resource")
    resource: ReferenceKind
    identifier: str

    def __init__(self, resource: ReferenceKind, identifier: str) -> None:
        """Initialize the missing resource identity."""
        super().__init__(resource, identifier)
        self.resource = resource
        self.identifier = identifier

    @override
    def __str__(self) -> str:
        """Render the missing reference."""
        return f"missing {self.resource} reference: {self.identifier}"


@final
class EventNotFoundError(Exception):
    """A requested event does not exist."""

    __slots__ = ("event_id",)
    event_id: EventId

    def __init__(self, event_id: EventId) -> None:
        """Initialize the missing event identity."""
        super().__init__(event_id)
        self.event_id = event_id

    @override
    def __str__(self) -> str:
        """Render the missing event."""
        return f"event not found: {self.event_id}"


def add_event(connection: SQLConnection, request: AddEventRequest) -> AddEventResult:
    """Validate references and atomically insert one manual event."""
    occurred_at = utc_text(request.occurred_at)
    confirmed_at = utc_text(request.confirmed_at)
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        _require_reference(
            connection,
            "SELECT 1 FROM journals WHERE id=?",
            "journal",
            request.journal_id,
        )
        _require_reference(
            connection,
            "SELECT 1 FROM trackers WHERE id=?",
            "tracker",
            request.tracker_id,
        )
        _require_reference(
            connection,
            "SELECT 1 FROM places WHERE id=?",
            "place",
            request.place_id,
        )
        _ = connection.execute(
            """INSERT INTO location_events
            (id,journal_id,tracker_id,place_id,event_type,occurred_at,confirmed_at,
             latitude,longitude,accuracy_m,source,status,note,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                request.event_id,
                request.journal_id,
                request.tracker_id,
                request.place_id,
                "manual",
                occurred_at,
                confirmed_at,
                request.latitude,
                request.longitude,
                request.accuracy_m,
                "manual",
                "confirmed",
                request.note,
                confirmed_at,
                confirmed_at,
            ),
        )
    except MissingEventReferenceError, sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()
    return AddEventResult(event_id=request.event_id)


def exclude_event(
    connection: SQLConnection, mutation: EventMutation
) -> EventMutationResult:
    """Mark an event excluded while retaining an audit revision."""
    return _change_status(connection, mutation, EventStatus.EXCLUDED)


def restore_event(
    connection: SQLConnection, mutation: EventMutation
) -> EventMutationResult:
    """Restore an excluded event while retaining an audit revision."""
    return _change_status(connection, mutation, EventStatus.CONFIRMED)


def _require_reference(
    connection: SQLConnection,
    query: str,
    resource: ReferenceKind,
    identifier: str,
) -> None:
    if connection.execute(query, (identifier,)).fetchone() is None:
        raise MissingEventReferenceError(resource, identifier)


def _change_status(
    connection: SQLConnection,
    mutation: EventMutation,
    target: EventStatus,
) -> EventMutationResult:
    changed_at = utc_text(mutation.changed_at)
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        old_status, old_changed_at = _event_audit_state(connection, mutation.event_id)
        _ = connection.execute(
            "UPDATE location_events SET status=?,updated_at=? WHERE id=? AND status<>?",
            (target.value, changed_at, mutation.event_id, target.value),
        )
        changes = connection.execute("SELECT changes()").fetchone()
        changes_value = None if changes is None else changes[0]
        if required_integer(changes_value, field="status changes") == 0:
            connection.rollback()
            return EventMutationResult(
                event_id=mutation.event_id,
                revision_id=None,
                status=target,
                changed=False,
            )
        revision_id = RevisionId(uuid4().hex)
        _ = connection.execute(
            """INSERT INTO event_revisions
            (id,event_id,old_data,new_data,reason,changed_by,changed_at)
            VALUES (?,?,?,?,?,?,?)""",
            (
                revision_id,
                mutation.event_id,
                _status_json(old_status, old_changed_at),
                _status_json(target.value, changed_at),
                mutation.reason,
                mutation.changed_by,
                changed_at,
            ),
        )
    except DatabaseSchemaError, EventNotFoundError, sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()
    return EventMutationResult(
        event_id=mutation.event_id,
        revision_id=revision_id,
        status=target,
        changed=True,
    )


def _status_json(status: str, changed_at: str) -> str:
    return json.dumps(
        {"changed_at": changed_at, "status": status},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _event_audit_state(connection: SQLConnection, event_id: EventId) -> tuple[str, str]:
    row = connection.execute(
        "SELECT status,updated_at FROM location_events WHERE id=?", (event_id,)
    ).fetchone()
    if row is None:
        raise EventNotFoundError(event_id)
    return (
        required_text(row[0], field="location_events.status"),
        required_text(row[1], field="location_events.updated_at"),
    )
