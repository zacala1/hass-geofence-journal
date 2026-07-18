"""Typed dispatch for the closed Geofence Journal service surface."""

from __future__ import annotations

from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final, Protocol

from .const import DOMAIN
from .export import ExportRequest
from .maintenance import (
    AddEventRequest,
    EventResponse,
    ExcludeEventRequest,
    ExportResponse,
    JournalEventPayload,
    PurgeEventsRequest,
    ResetDatabaseRequest,
    ResourceResponse,
    RestoreEventRequest,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
    journal_event_data,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse

    from .storage.maintenance import CompactResult, PurgeResult, ResetResult


@unique
class ServiceAction(StrEnum):
    """Closed Home Assistant management surface."""

    UPSERT_TRACKER = "upsert_tracker"
    UPSERT_PLACE = "upsert_place"
    UPSERT_JOURNAL = "upsert_journal"
    UPSERT_RULE = "upsert_rule"
    ADD_EVENT = "add_event"
    EXCLUDE_EVENT = "exclude_event"
    RESTORE_EVENT = "restore_event"
    EXPORT_JOURNAL = "export_journal"
    PURGE_EVENTS = "purge_events"
    COMPACT_DATABASE = "compact_database"
    RESET_DATABASE = "reset_database"


SERVICE_ACTIONS: Final = tuple(ServiceAction)
EVENT_JOURNAL: Final = f"{DOMAIN}_event"
RESOURCE_ACTIONS: Final = frozenset(
    {
        ServiceAction.UPSERT_TRACKER,
        ServiceAction.UPSERT_PLACE,
        ServiceAction.UPSERT_JOURNAL,
        ServiceAction.UPSERT_RULE,
    }
)
EVENT_ACTIONS: Final = frozenset(
    {
        ServiceAction.ADD_EVENT,
        ServiceAction.EXCLUDE_EVENT,
        ServiceAction.RESTORE_EVENT,
    }
)


class ServicesBackend(Protocol):
    """Manager-owned operations injected into the HA service boundary."""

    async def async_upsert_tracker(
        self, request: UpsertTrackerRequest
    ) -> ResourceResponse:
        """Create or update one tracker."""
        ...

    async def async_upsert_place(self, request: UpsertPlaceRequest) -> ResourceResponse:
        """Create or update one place."""
        ...

    async def async_upsert_journal(
        self, request: UpsertJournalRequest
    ) -> ResourceResponse:
        """Create or update one journal."""
        ...

    async def async_upsert_rule(self, request: UpsertRuleRequest) -> ResourceResponse:
        """Create or update one linked rule."""
        ...

    async def async_refresh_resources(self) -> None:
        """Refresh manager-owned runnable resources."""
        ...

    async def async_add_event(
        self, request: AddEventRequest, user_id: str | None
    ) -> EventResponse:
        """Create one manual event."""
        ...

    async def async_exclude_event(
        self, request: ExcludeEventRequest, user_id: str | None
    ) -> EventResponse:
        """Exclude one retained event."""
        ...

    async def async_restore_event(
        self, request: RestoreEventRequest, user_id: str | None
    ) -> EventResponse:
        """Restore one retained event."""
        ...

    async def async_export_journal(self, request: ExportRequest) -> ExportResponse:
        """Create one short-lived CSV export."""
        ...

    async def async_purge_events(self, request: PurgeEventsRequest) -> PurgeResult:
        """Dry-run or permanently purge old events."""
        ...

    async def async_compact_database(self) -> CompactResult:
        """Checkpoint and compact the database."""
        ...

    async def async_reset_database(self, request: ResetDatabaseRequest) -> ResetResult:
        """Reset all integration data after exact confirmation."""
        ...


def async_fire_journal_event(hass: HomeAssistant, payload: JournalEventPayload) -> None:
    """Fire one coordinate-free journal event on Home Assistant's bus."""
    hass.bus.async_fire(EVENT_JOURNAL, journal_event_data(payload))


async def async_dispatch_service(
    action: ServiceAction, call: ServiceCall, backend: ServicesBackend
) -> ServiceResponse:
    """Dispatch one validated administrator action to its typed operation."""
    if action in RESOURCE_ACTIONS:
        return await _dispatch_resource(action, call, backend)
    if action in EVENT_ACTIONS:
        return await _dispatch_event(action, call, backend)
    return await _dispatch_lifecycle(action, call, backend)


async def _dispatch_resource(
    action: ServiceAction, call: ServiceCall, backend: ServicesBackend
) -> ServiceResponse:
    match action:
        case ServiceAction.UPSERT_TRACKER:
            response = await backend.async_upsert_tracker(
                UpsertTrackerRequest.model_validate(call.data)
            )
        case ServiceAction.UPSERT_PLACE:
            response = await backend.async_upsert_place(
                UpsertPlaceRequest.model_validate(call.data)
            )
        case ServiceAction.UPSERT_JOURNAL:
            response = await backend.async_upsert_journal(
                UpsertJournalRequest.model_validate(call.data)
            )
        case ServiceAction.UPSERT_RULE:
            response = await backend.async_upsert_rule(
                UpsertRuleRequest.model_validate(call.data)
            )
        case _:
            raise RuntimeError(action)
    await backend.async_refresh_resources()
    return {"resource_id": str(response.resource_id)}


async def _dispatch_event(
    action: ServiceAction, call: ServiceCall, backend: ServicesBackend
) -> ServiceResponse:
    match action:
        case ServiceAction.ADD_EVENT:
            response = await backend.async_add_event(
                AddEventRequest.model_validate(call.data), call.context.user_id
            )
        case ServiceAction.EXCLUDE_EVENT:
            response = await backend.async_exclude_event(
                ExcludeEventRequest.model_validate(call.data), call.context.user_id
            )
        case ServiceAction.RESTORE_EVENT:
            response = await backend.async_restore_event(
                RestoreEventRequest.model_validate(call.data), call.context.user_id
            )
        case _:
            raise RuntimeError(action)
    if response.changed:
        async_fire_journal_event(call.hass, response.payload)
    return {
        "changed": response.changed,
        "payload": journal_event_data(response.payload),
    }


async def _dispatch_lifecycle(
    action: ServiceAction, call: ServiceCall, backend: ServicesBackend
) -> ServiceResponse:
    match action:
        case ServiceAction.EXPORT_JOURNAL:
            response = await backend.async_export_journal(
                ExportRequest.model_validate(call.data)
            )
            return {
                "url": response.url,
                "expires_at": response.expires_at,
                "count": response.count,
            }
        case ServiceAction.PURGE_EVENTS:
            response = await backend.async_purge_events(
                PurgeEventsRequest.model_validate(call.data)
            )
            return {
                "matched_events": response.matched_events,
                "matched_revisions": response.matched_revisions,
                "deleted_events": response.deleted_events,
                "deleted_revisions": response.deleted_revisions,
                "dry_run": response.dry_run,
            }
        case ServiceAction.COMPACT_DATABASE:
            response = await backend.async_compact_database()
            return {
                "database_bytes_before": response.database_bytes_before,
                "database_bytes_after": response.database_bytes_after,
                "wal_bytes_before": response.wal_bytes_before,
                "wal_bytes_after": response.wal_bytes_after,
                "checkpoint_log_pages": response.checkpoint_log_pages,
                "checkpointed_pages": response.checkpointed_pages,
                "checkpoint_busy": response.checkpoint_busy,
            }
        case ServiceAction.RESET_DATABASE:
            response = await backend.async_reset_database(
                ResetDatabaseRequest.model_validate(call.data)
            )
            return {
                "deleted_trackers": response.deleted_trackers,
                "deleted_places": response.deleted_places,
                "deleted_journals": response.deleted_journals,
                "deleted_rules": response.deleted_rules,
                "deleted_events": response.deleted_events,
                "deleted_revisions": response.deleted_revisions,
                "deleted_runtime_states": response.deleted_runtime_states,
                "schema_version": response.schema_version,
            }
        case _:
            raise RuntimeError(action)
