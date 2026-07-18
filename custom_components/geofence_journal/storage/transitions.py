"""Atomic event and runtime-state transition persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

from custom_components.geofence_journal.models import PresenceState

from .db_types import required_integer, required_text
from .errors import InjectedStorageFaultError
from .records import (
    ConfirmedTransition,
    RuntimeStateRecord,
    TransitionResult,
    utc_text,
)

if TYPE_CHECKING:
    from .db_types import SQLConnection


def confirm_transition(
    connection: SQLConnection,
    transition: ConfirmedTransition,
    *,
    fail_after_event_insert: bool = False,
) -> TransitionResult:
    """Atomically append an idempotent event and replace runtime state."""
    occurred_at = utc_text(transition.occurred_at)
    confirmed_at = utc_text(transition.confirmed_at)
    deadline = utc_text(transition.confirmed_deadline)
    existing = connection.execute(
        """SELECT id FROM location_events
        WHERE rule_id=? AND transition_generation=? AND confirmed_deadline=?""",
        (transition.rule_id, transition.generation, deadline),
    ).fetchone()
    if existing is not None:
        return TransitionResult(
            event_id=required_text(existing[0], field="location_events.id"),
            created=False,
        )
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        _ = connection.execute(
            """INSERT INTO location_events
            (id,journal_id,rule_id,tracker_id,place_id,event_type,occurred_at,
             confirmed_at,source,status,transition_generation,confirmed_deadline,
             created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                transition.event_id,
                transition.journal_id,
                transition.rule_id,
                transition.tracker_id,
                transition.place_id,
                transition.event_type.value,
                occurred_at,
                confirmed_at,
                transition.source.value,
                "confirmed",
                transition.generation,
                deadline,
                confirmed_at,
                confirmed_at,
            ),
        )
        if fail_after_event_insert:
            _raise_injected_fault()
        _ = connection.execute(
            """INSERT INTO runtime_states
            (rule_id,presence_state,last_event_id,last_event_type,last_event_at,
             updated_at) VALUES (?,?,?,?,?,?)
            ON CONFLICT(rule_id) DO UPDATE SET
            presence_state=excluded.presence_state,
            last_event_id=excluded.last_event_id,
            last_event_type=excluded.last_event_type,
            last_event_at=excluded.last_event_at,updated_at=excluded.updated_at""",
            (
                transition.rule_id,
                transition.target_state.value,
                transition.event_id,
                transition.event_type.value,
                confirmed_at,
                confirmed_at,
            ),
        )
    except InjectedStorageFaultError, sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()
    return TransitionResult(event_id=transition.event_id, created=True)


def event_count(connection: SQLConnection) -> int:
    """Return the number of persisted location events."""
    row = connection.execute("SELECT COUNT(*) FROM location_events").fetchone()
    if row is None:
        return 0
    return required_integer(row[0], field="event count")


def runtime_state(connection: SQLConnection, rule_id: str) -> RuntimeStateRecord | None:
    """Load the persisted runtime state for a rule."""
    row = connection.execute(
        """SELECT presence_state,last_event_id,updated_at
        FROM runtime_states WHERE rule_id=?""",
        (rule_id,),
    ).fetchone()
    if row is None:
        return None
    return RuntimeStateRecord(
        rule_id=rule_id,
        presence_state=PresenceState(
            required_text(row[0], field="runtime_states.presence_state")
        ),
        last_event_id=(
            None
            if row[1] is None
            else required_text(row[1], field="runtime_states.last_event_id")
        ),
        updated_at=datetime.fromisoformat(
            required_text(row[2], field="runtime_states.updated_at")
        ),
    )


def _raise_injected_fault() -> None:
    raise InjectedStorageFaultError(stage="event-insert")
