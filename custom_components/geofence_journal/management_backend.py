"""Concrete SQLite adapter for Geofence Journal management services."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from anyio.to_thread import run_sync

import custom_components.geofence_journal.resource_catalog as catalog

from .export import (
    ExportArtifact,
    ExportClock,
    ExportRegistry,
    ExportRequest,
    export_journal_csv,
)
from .maintenance import (
    AddEventRequest,
    EventResponse,
    ExcludeEventRequest,
    ExportResponse,
    PurgeEventsRequest,
    ResetDatabaseRequest,
    ResourceResponse,
    RestoreEventRequest,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
)
from .management_events import (
    async_add_manual_event,
    async_exclude_retained_event,
    async_restore_retained_event,
)
from .management_resources import (
    async_upsert_journal_resource,
    async_upsert_place_resource,
    async_upsert_rule_resource,
    async_upsert_tracker_resource,
)
from .models import JournalId
from .storage.errors import StorageError
from .storage.events import MissingEventReferenceError
from .storage.maintenance import (
    CompactResult,
    MaintenanceCoordinator,
    PurgeRequest,
    PurgeResult,
    ResetRequest,
    ResetResult,
    compact_database,
    purge_events,
    reset_database,
)
from .storage.resource_catalog import delete_resource, get_resource, list_resources

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from uuid import UUID

    from .settings import Settings
    from .storage.async_adapter import AsyncSQLiteStore
    from .storage.db_types import SQLConnection


@dataclass(frozen=True, slots=True)
class ManagementBackendDependencies:
    """Lifecycle, privacy, and export capabilities used by the backend."""

    exports: ExportRegistry
    coordinator: MaintenanceCoordinator
    clock: ExportClock
    settings: Settings
    schedule_export_cleanup: Callable[[ExportArtifact], None]
    on_event: Callable[[datetime], None]


@final
class SQLiteManagementBackend:
    """Run typed management operations through one serialized SQLite store."""

    __slots__ = (
        "_clock",
        "_coordinator",
        "_exports",
        "_on_event",
        "_schedule_export_cleanup",
        "_settings",
        "_store",
    )

    def __init__(
        self, store: AsyncSQLiteStore, dependencies: ManagementBackendDependencies
    ) -> None:
        """Bind persistence, lifecycle, privacy, export, and refresh capabilities."""
        self._store = store
        self._exports = dependencies.exports
        self._coordinator = dependencies.coordinator
        self._clock = dependencies.clock
        self._settings = dependencies.settings
        self._schedule_export_cleanup = dependencies.schedule_export_cleanup
        self._on_event = dependencies.on_event

    async def async_upsert_tracker(
        self, request: UpsertTrackerRequest
    ) -> ResourceResponse:
        """Create or update one tracker definition."""
        async with self._coordinator.pause_and_drain():
            return await async_upsert_tracker_resource(
                self._store, self._clock, request
            )

    async def async_upsert_place(self, request: UpsertPlaceRequest) -> ResourceResponse:
        """Create or update one place definition."""
        async with self._coordinator.pause_and_drain():
            return await async_upsert_place_resource(
                self._store, self._clock, self._settings, request
            )

    async def async_upsert_journal(
        self, request: UpsertJournalRequest
    ) -> ResourceResponse:
        """Create or update one journal definition."""
        async with self._coordinator.pause_and_drain():
            return await async_upsert_journal_resource(
                self._store, self._clock, request
            )

    async def async_upsert_rule(self, request: UpsertRuleRequest) -> ResourceResponse:
        """Create or update one linked recording rule."""
        async with self._coordinator.pause_and_drain():
            return await async_upsert_rule_resource(
                self._store, self._clock, self._settings, request
            )

    async def async_list_resources(
        self, request: catalog.ListResourcesRequest
    ) -> catalog.ResourceListResponse:
        """List configured resources from an independent read snapshot."""
        resources = await self._store.async_run_read_operation(
            lambda connection: list_resources(
                connection,
                request.resource_type,
                include_disabled=request.include_disabled,
            )
        )
        return catalog.ResourceListResponse(resources)

    async def async_get_resource(
        self, request: catalog.GetResourceRequest
    ) -> catalog.ResourceGetResponse:
        """Read one configured resource from an independent snapshot."""
        resource = await self._store.async_run_read_operation(
            lambda connection: get_resource(
                connection, request.resource_type, str(request.resource_id)
            )
        )
        return catalog.ResourceGetResponse(resource)

    async def async_delete_resource(
        self, request: catalog.DeleteResourceRequest
    ) -> catalog.ResourceDeleteResponse:
        """Pause and rebuild observations around one configuration delete."""
        async with self._coordinator.pause_and_drain():
            return await self._store.async_run_operation(
                lambda connection: delete_resource(
                    connection, request.resource_type, str(request.resource_id)
                )
            )

    async def async_add_event(
        self, request: AddEventRequest, user_id: str | None
    ) -> EventResponse:
        """Create one privacy-filtered manual journal event."""
        response = await async_add_manual_event(
            self._store,
            self._clock,
            request,
            user_id,
            store_coordinates=self._settings.store_coordinates,
        )
        if response.changed:
            self._on_event(request.occurred_at)
        return response

    async def async_exclude_event(
        self, request: ExcludeEventRequest, user_id: str | None
    ) -> EventResponse:
        """Exclude one retained event with an audit revision."""
        return await async_exclude_retained_event(
            self._store, self._clock, request.event_id, request.reason, user_id
        )

    async def async_restore_event(
        self, request: RestoreEventRequest, user_id: str | None
    ) -> EventResponse:
        """Restore one retained event with an audit revision."""
        return await async_restore_retained_event(
            self._store, self._clock, request.event_id, request.reason, user_id
        )

    async def async_export_journal(self, request: ExportRequest) -> ExportResponse:
        """Create, schedule, and describe one authenticated CSV export."""
        await self._store.async_run_operation(
            lambda connection: _require_journal(connection, request.journal_id)
        )
        artifact = await run_sync(self._exports.allocate)
        effective = request.model_copy(
            update={
                "include_coordinates": (
                    request.include_coordinates and self._settings.store_coordinates
                )
            }
        )
        try:
            count = await self._store.async_run_read_operation(
                lambda connection: export_journal_csv(
                    connection, artifact.path, effective
                )
            )
            self._schedule_export_cleanup(artifact)
        except OSError, RuntimeError, sqlite3.Error, StorageError:
            await run_sync(self._exports.discard, artifact.export_id)
            raise
        expires_at = artifact.expires_at.isoformat().replace("+00:00", "Z")
        return ExportResponse(artifact.url, expires_at, count)

    async def async_purge_events(self, request: PurgeEventsRequest) -> PurgeResult:
        """Dry-run or pause observations around one confirmed event purge."""
        await self._store.async_run_operation(
            lambda connection: _require_journal(connection, request.journal_id)
        )
        storage_request = PurgeRequest(
            request.before,
            JournalId(str(request.journal_id)),
            request.dry_run,
            request.confirm,
        )

        def operation(connection: SQLConnection) -> PurgeResult:
            return purge_events(connection, storage_request)

        if request.dry_run:
            return await self._store.async_run_operation(operation)
        async with self._coordinator.pause_and_drain():
            return await self._store.async_run_operation(operation)

    async def async_compact_database(self) -> CompactResult:
        """Pause observations around WAL checkpoint and VACUUM work."""
        async with self._coordinator.pause_and_drain():
            return await self._store.async_run_exclusive_operation(compact_database)

    async def async_reset_database(self, request: ResetDatabaseRequest) -> ResetResult:
        """Reset storage and invalidate exports inside one pause scope."""
        async with self._coordinator.pause_and_drain():
            result = await self._store.async_run_exclusive_operation(
                lambda connection: reset_database(
                    connection, ResetRequest(request.confirmation)
                )
            )
            await run_sync(self._exports.discard_all)
            return result


def _require_journal(connection: SQLConnection, journal_id: UUID) -> None:
    row = connection.execute(
        "SELECT 1 FROM journals WHERE id=?", (str(journal_id),)
    ).fetchone()
    if row is None:
        resource = "journal"
        raise MissingEventReferenceError(resource, str(journal_id))
