"""Async lifecycle wrapper for configured-retention purge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .retention import PurgeRetentionRequest, purge_configured_retention

if TYPE_CHECKING:
    from .export import ExportClock
    from .storage.async_adapter import AsyncSQLiteStore
    from .storage.db_types import SQLConnection
    from .storage.maintenance import MaintenanceCoordinator, PurgeResult


async def async_purge_configured_retention(
    store: AsyncSQLiteStore,
    coordinator: MaintenanceCoordinator,
    clock: ExportClock,
    request: PurgeRetentionRequest,
) -> PurgeResult:
    """Run dry-run directly or pause observations around confirmed deletion."""

    def operation(connection: SQLConnection) -> PurgeResult:
        return purge_configured_retention(connection, clock.utc_now(), request)

    if request.dry_run:
        return await store.async_run_operation(operation)
    async with coordinator.pause_and_drain():
        return await store.async_run_operation(operation)
