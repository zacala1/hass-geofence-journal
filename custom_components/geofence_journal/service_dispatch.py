"""Typed dispatch for the closed Geofence Journal service surface."""

from __future__ import annotations

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
from .service_actions import (
    CATALOG_ACTIONS,
    EVENT_ACTIONS,
    RESOURCE_ACTIONS,
    ServiceAction,
)
from .service_catalog_dispatch import async_dispatch_catalog

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse

    from .resource_catalog import (
        DeleteResourceRequest,
        GetResourceRequest,
        ListResourcesRequest,
        ResourceDeleteResponse,
        ResourceGetResponse,
        ResourceListResponse,
    )
    from .storage.maintenance import CompactResult, PurgeResult, ResetResult


EVENT_JOURNAL: Final = f"{DOMAIN}_event"


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

    async def async_list_resources(
        self, request: ListResourcesRequest
    ) -> ResourceListResponse:
        """List configured resources without blocking observation writes."""
        ...

    async def async_get_resource(
        self, request: GetResourceRequest
    ) -> ResourceGetResponse:
        """Read one exact configured resource."""
        ...

    async def async_delete_resource(
        self, request: DeleteResourceRequest
    ) -> ResourceDeleteResponse:
        """Delete one explicitly confirmed, unreferenced resource."""
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
    if action in CATALOG_ACTIONS:
        return await async_dispatch_catalog(action, call, backend)
    if action in RESOURCE_ACTIONS:
        return await _dispatch_resource(action, call, backend)
    if action in EVENT_ACTIONS:
        return await _dispatch_event(action, call, backend)
    return await _dispatch_lifecycle(action, call, backend)


async def _dispatch_resource(
    action: ServiceAction, call: ServiceCall, backend: ServicesBackend
) -> ServiceResponse:
    if action is ServiceAction.UPSERT_TRACKER:
        response = await backend.async_upsert_tracker(
            UpsertTrackerRequest.model_validate(call.data)
        )
    elif action is ServiceAction.UPSERT_PLACE:
        response = await backend.async_upsert_place(
            UpsertPlaceRequest.model_validate(call.data)
        )
    elif action is ServiceAction.UPSERT_JOURNAL:
        response = await backend.async_upsert_journal(
            UpsertJournalRequest.model_validate(call.data)
        )
    elif action is ServiceAction.UPSERT_RULE:
        response = await backend.async_upsert_rule(
            UpsertRuleRequest.model_validate(call.data)
        )
    else:
        raise RuntimeError(action)
    return {"resource_id": str(response.resource_id)}


async def _dispatch_event(
    action: ServiceAction, call: ServiceCall, backend: ServicesBackend
) -> ServiceResponse:
    if action is ServiceAction.ADD_EVENT:
        response = await backend.async_add_event(
            AddEventRequest.model_validate(call.data), call.context.user_id
        )
    elif action is ServiceAction.EXCLUDE_EVENT:
        response = await backend.async_exclude_event(
            ExcludeEventRequest.model_validate(call.data), call.context.user_id
        )
    elif action is ServiceAction.RESTORE_EVENT:
        response = await backend.async_restore_event(
            RestoreEventRequest.model_validate(call.data), call.context.user_id
        )
    else:
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
    if action is ServiceAction.EXPORT_JOURNAL:
        response = await backend.async_export_journal(
            ExportRequest.model_validate(call.data)
        )
        return {
            "url": response.url,
            "expires_at": response.expires_at,
            "count": response.count,
        }
    if action is ServiceAction.PURGE_EVENTS:
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
    if action is ServiceAction.COMPACT_DATABASE:
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
    if action is ServiceAction.RESET_DATABASE:
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
    raise RuntimeError(action)
