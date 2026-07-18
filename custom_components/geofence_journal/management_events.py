"""Manual and retained event management over the async SQLite store."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from .maintenance import (
    AddEventRequest,
    EventResponse,
    JournalEventPayload,
    journal_event_data,
    transition_event_payload,
)
from .models import EventId, JournalId, PlaceId, TrackerId
from .storage.db_types import required_text
from .storage.events import (
    AddEventRequest as StorageAddEventRequest,
)
from .storage.events import (
    EventMutation,
    EventMutationResult,
    EventNotFoundError,
    add_event,
    exclude_event,
    restore_event,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .export import ExportClock
    from .storage.async_adapter import AsyncSQLiteStore
    from .storage.db_types import SQLConnection

__all__ = ("journal_event_data", "transition_event_payload")


async def async_add_manual_event(
    store: AsyncSQLiteStore,
    clock: ExportClock,
    request: AddEventRequest,
    user_id: str | None,
    *,
    store_coordinates: bool,
) -> EventResponse:
    """Persist one manual event while enforcing coordinate privacy."""
    _ = user_id
    event_id = EventId(str(uuid4()))
    coordinates = store_coordinates and request.latitude is not None
    storage_request = StorageAddEventRequest(
        event_id=event_id,
        journal_id=JournalId(str(request.journal_id)),
        tracker_id=TrackerId(str(request.tracker_id)),
        place_id=PlaceId(str(request.place_id)),
        occurred_at=request.occurred_at,
        confirmed_at=clock.utc_now(),
        latitude=request.latitude if coordinates else None,
        longitude=request.longitude if coordinates else None,
        accuracy_m=request.accuracy_m if coordinates else None,
        note=request.note,
    )
    payload = await store.async_run_operation(
        lambda connection: _add_and_read(connection, storage_request)
    )
    return EventResponse(changed=True, payload=payload)


async def async_exclude_retained_event(
    store: AsyncSQLiteStore,
    clock: ExportClock,
    event_id: UUID,
    reason: str | None,
    user_id: str | None,
) -> EventResponse:
    """Exclude one retained event and return its current public payload."""
    mutation = EventMutation(EventId(str(event_id)), clock.utc_now(), user_id, reason)
    return await _async_mutate(store, mutation, exclude_event)


async def async_restore_retained_event(
    store: AsyncSQLiteStore,
    clock: ExportClock,
    event_id: UUID,
    reason: str | None,
    user_id: str | None,
) -> EventResponse:
    """Restore one retained event and return its current public payload."""
    mutation = EventMutation(EventId(str(event_id)), clock.utc_now(), user_id, reason)
    return await _async_mutate(store, mutation, restore_event)


async def _async_mutate(
    store: AsyncSQLiteStore,
    mutation: EventMutation,
    operation: Callable[[SQLConnection, EventMutation], EventMutationResult],
) -> EventResponse:
    def mutate_and_read(connection: SQLConnection) -> EventResponse:
        result = operation(connection, mutation)
        return EventResponse(
            result.changed, _event_payload(connection, result.event_id)
        )

    return await store.async_run_operation(mutate_and_read)


def _add_and_read(
    connection: SQLConnection, request: StorageAddEventRequest
) -> JournalEventPayload:
    result = add_event(connection, request)
    return _event_payload(connection, result.event_id)


def _event_payload(connection: SQLConnection, event_id: EventId) -> JournalEventPayload:
    row = connection.execute(
        """SELECT e.id,e.journal_id,j.name,e.rule_id,e.tracker_id,t.display_name,
        e.place_id,p.name,e.event_type,e.status,e.occurred_at FROM location_events e
        JOIN journals j ON j.id=e.journal_id JOIN trackers t ON t.id=e.tracker_id
        JOIN places p ON p.id=e.place_id WHERE e.id=?""",
        (event_id,),
    ).fetchone()
    if row is None:
        raise EventNotFoundError(event_id)
    rule_id = None if row[3] is None else required_text(row[3], field="event rule id")
    return JournalEventPayload(
        event_id=required_text(row[0], field="event id"),
        journal_id=required_text(row[1], field="event journal id"),
        journal_name=required_text(row[2], field="journal name"),
        rule_id=rule_id,
        tracker_id=required_text(row[4], field="event tracker id"),
        tracker_name=required_text(row[5], field="tracker name"),
        place_id=required_text(row[6], field="event place id"),
        place_name=required_text(row[7], field="place name"),
        event_type=required_text(row[8], field="event type"),
        status=required_text(row[9], field="event status"),
        timestamp=required_text(row[10], field="event timestamp"),
    )
