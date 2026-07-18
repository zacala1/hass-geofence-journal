"""Immutable records crossing the SQLite repository boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.geofence_journal.models import (
        LocationEventType,
        LocationSource,
        PresenceState,
    )

from .errors import DatabaseSchemaError


def utc_text(value: datetime) -> str:
    """Encode a timezone-aware UTC datetime in canonical ISO-8601 form."""
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise DatabaseSchemaError(detail="datetime must be timezone-aware UTC")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class StorageDiagnostics:
    """Connection invariants observable by lifecycle diagnostics."""

    schema_version: int
    journal_mode: str
    foreign_keys_enabled: bool
    busy_timeout_ms: int


@dataclass(frozen=True, slots=True)
class RuntimeStateRecord:
    """Persisted state for a single recording rule."""

    rule_id: str
    presence_state: PresenceState
    last_event_id: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Exactly-once result from confirming a transition."""

    event_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class ConfirmedTransition:
    """A confirmed geofence transition committed with its runtime state."""

    event_id: str
    rule_id: str
    tracker_id: str
    place_id: str
    journal_id: str
    event_type: LocationEventType
    source: LocationSource
    target_state: PresenceState
    occurred_at: datetime
    confirmed_at: datetime
    generation: int
    confirmed_deadline: datetime
