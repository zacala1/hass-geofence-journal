from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.util import find_spec
from typing import TYPE_CHECKING, Final
from uuid import UUID

import pytest
from custom_components.geofence_journal import maintenance, services
from custom_components.geofence_journal.export import ExportArtifact, ExportRegistry
from custom_components.geofence_journal.maintenance import (
    PurgeEventsRequest,
    ResetDatabaseRequest,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertTrackerRequest,
)
from custom_components.geofence_journal.management_backend import (
    ManagementBackendDependencies,
    SQLiteManagementBackend,
)
from custom_components.geofence_journal.models import PlaceKind, TrackerKind
from custom_components.geofence_journal.storage import AsyncSQLiteStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
TRACKER_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = UUID("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = UUID("00000000-0000-4000-8000-000000000003")


class FakeClock:
    def utc_now(self) -> datetime:
        return NOW


class RecordingCoordinator:
    def __init__(self) -> None:
        self.steps: list[str] = []

    @asynccontextmanager
    async def pause_and_drain(self) -> AsyncGenerator[None]:
        self.steps.append("pause")
        try:
            yield
        finally:
            self.steps.append("resume")


async def _backend(
    tmp_path: Path, *, store_coordinates: bool = False
) -> tuple[
    SQLiteManagementBackend,
    AsyncSQLiteStore,
    RecordingCoordinator,
    list[ExportArtifact],
]:
    store = AsyncSQLiteStore(tmp_path / "backend.db")
    await store.async_open()
    coordinator = RecordingCoordinator()
    scheduled: list[ExportArtifact] = []

    async def refresh() -> None:
        coordinator.steps.append("refresh")

    backend = SQLiteManagementBackend(
        store,
        ManagementBackendDependencies(
            exports=ExportRegistry(tmp_path / "exports", FakeClock()),
            coordinator=coordinator,
            clock=FakeClock(),
            store_coordinates=store_coordinates,
            refresh_resources=refresh,
            schedule_export_cleanup=scheduled.append,
        ),
    )
    return backend, store, coordinator, scheduled


async def _seed(backend: SQLiteManagementBackend) -> None:
    _ = await backend.async_upsert_tracker(
        UpsertTrackerRequest(
            resource_id=TRACKER_ID,
            entity_id="person.alice",
            kind=TrackerKind.PERSON,
            name="Alice",
        )
    )
    _ = await backend.async_upsert_place(
        UpsertPlaceRequest(
            resource_id=PLACE_ID,
            name="Home",
            source_type=PlaceKind.COORDINATE,
            latitude=37.5,
            longitude=127.0,
            radius_meters=100,
            exit_margin_meters=25,
        )
    )
    _ = await backend.async_upsert_journal(
        UpsertJournalRequest(resource_id=JOURNAL_ID, name="Presence")
    )


def test_management_backend_public_contract_is_available() -> None:
    # Given: Task 5 needs a concrete adapter without importing the manager.
    required_requests = {
        "AddEventRequest",
        "ExcludeEventRequest",
        "RestoreEventRequest",
        "PurgeEventsRequest",
        "ResetDatabaseRequest",
    }
    required_protocol_methods = {
        "async_add_event",
        "async_compact_database",
        "async_exclude_event",
        "async_export_journal",
        "async_purge_events",
        "async_reset_database",
        "async_restore_event",
    }

    # When: the management seam is inspected before runtime wiring.
    backend_spec = find_spec("custom_components.geofence_journal.management_backend")

    # Then: every request and operation needed by Task 5 has a stable import.
    assert backend_spec is not None
    assert required_requests <= set(dir(maintenance))
    assert required_protocol_methods <= set(dir(services.ServicesBackend))


@pytest.mark.asyncio
async def test_upserts_generate_stable_ids_and_persist_place_margin(
    tmp_path: Path,
) -> None:
    # Given: an open real store and one create request without an identifier.
    backend, store, _, _ = await _backend(tmp_path)

    # When: the resource is created and then read through SQLite.
    response = await backend.async_upsert_place(
        UpsertPlaceRequest(
            name="Home",
            source_type=PlaceKind.COORDINATE,
            latitude=37.5,
            longitude=127,
            radius_meters=100,
            exit_margin_meters=25,
        )
    )
    row = await store.async_run_operation(
        lambda connection: connection.execute(
            "SELECT id,exit_margin_m FROM places"
        ).fetchone()
    )
    await store.async_close()

    # Then: one UUID is generated and the place-owned margin is stored.
    assert row == (str(response.resource_id), 25.0)


@pytest.mark.asyncio
async def test_purge_and_reset_use_pause_refresh_resume_order(
    tmp_path: Path,
) -> None:
    # Given: one populated backend and a confirmed destructive cutoff.
    backend, store, coordinator, _ = await _backend(tmp_path)
    await _seed(backend)

    # When: purge and reset run through their lifecycle scopes.
    _ = await backend.async_purge_events(
        PurgeEventsRequest(
            before=NOW,
            journal_id=JOURNAL_ID,
            dry_run=False,
            confirm=True,
        )
    )
    _ = await backend.async_reset_database(
        ResetDatabaseRequest(confirmation="DELETE ALL GEOFENCE JOURNAL DATA")
    )
    await store.async_close()

    # Then: coordinator exit alone rebuilds listeners and resumes without deadlock.
    assert coordinator.steps == ["pause", "resume", "pause", "resume"]
