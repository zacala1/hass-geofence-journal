"""Typed, privacy-safe responses shared by services and runtime events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import EventStatus
from .runtime.state import RuntimeInvariantError
from .storage.records import utc_text

if TYPE_CHECKING:
    from homeassistant.util.json import JsonObjectType

    from .storage.records import RuntimeStateRecord, TransitionResult
    from .storage.resources import ConfiguredResources


@dataclass(frozen=True, slots=True)
class JournalEventPayload:
    """Coordinate-free public payload for one journal event change."""

    event_id: str
    journal_id: str
    journal_name: str
    rule_id: str | None
    tracker_id: str
    tracker_name: str
    place_id: str
    place_name: str
    event_type: str
    status: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class EventResponse:
    """One event mutation result and its coordinate-free bus payload."""

    changed: bool
    payload: JournalEventPayload


@dataclass(frozen=True, slots=True)
class ExportResponse:
    """Opaque authenticated export location and expiry metadata."""

    url: str
    expires_at: str
    count: int


def journal_event_data(payload: JournalEventPayload) -> JsonObjectType:
    """Convert one immutable payload to Home Assistant's dictionary shape."""
    return {
        "event_id": payload.event_id,
        "journal_id": payload.journal_id,
        "journal_name": payload.journal_name,
        "rule_id": payload.rule_id,
        "tracker_id": payload.tracker_id,
        "tracker_name": payload.tracker_name,
        "place_id": payload.place_id,
        "place_name": payload.place_name,
        "event_type": payload.event_type,
        "status": payload.status,
        "timestamp": payload.timestamp,
    }


def transition_event_payload(
    result: TransitionResult,
    state: RuntimeStateRecord,
    resources: ConfiguredResources,
) -> JournalEventPayload:
    """Build the shared payload for one newly committed runtime transition."""
    event_type = state.last_event_type
    timestamp = state.last_event_at
    if event_type is None or timestamp is None:
        raise RuntimeInvariantError(
            detail="committed transition is missing event metadata"
        )
    return JournalEventPayload(
        event_id=result.event_id,
        journal_id=str(resources.journal.journal_id),
        journal_name=resources.journal.name,
        rule_id=str(resources.rule.rule_id),
        tracker_id=str(resources.tracker.tracker_id),
        tracker_name=resources.tracker.name,
        place_id=str(resources.place.place_id),
        place_name=resources.place.name,
        event_type=event_type.value,
        status=EventStatus.CONFIRMED.value,
        timestamp=utc_text(timestamp),
    )
