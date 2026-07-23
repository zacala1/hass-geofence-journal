from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final
from uuid import UUID

import pytest
from custom_components.geofence_journal.export import ExportArtifact, ExportRegistry
from custom_components.geofence_journal.maintenance import (
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
)
from custom_components.geofence_journal.management_backend import (
    ManagementBackendDependencies,
    SQLiteManagementBackend,
)
from custom_components.geofence_journal.models import (
    Meters,
    PlaceKind,
    Seconds,
    TrackerKind,
)
from custom_components.geofence_journal.resource_catalog import (
    DeleteResourceRequest,
    GetResourceRequest,
    ListResourcesRequest,
    PlaceResourceItem,
    ResourceInUseError,
    ResourceType,
)
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import AsyncSQLiteStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

NOW: Final = datetime(2026, 7, 23, 12, tzinfo=UTC)
TRACKER_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = UUID("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = UUID("00000000-0000-4000-8000-000000000003")
RULE_ID: Final = UUID("00000000-0000-4000-8000-000000000004")


class FixedClock:
    def utc_now(self) -> datetime:
        return NOW


@final
class PauseRecorder:
    def __init__(self) -> None:
        self.pauses = 0
        self.resumes = 0

    @asynccontextmanager
    async def pause_and_drain(self) -> AsyncGenerator[None]:
        self.pauses += 1
        try:
            yield
        finally:
            self.resumes += 1


async def test_backend_catalog_reads_without_pause_and_rebuilds_after_delete(
    tmp_path: Path,
) -> None:
    store = AsyncSQLiteStore(tmp_path / "catalog-backend.db")
    await store.async_open()
    coordinator = PauseRecorder()
    scheduled: list[ExportArtifact] = []
    backend = SQLiteManagementBackend(
        store,
        ManagementBackendDependencies(
            exports=ExportRegistry(tmp_path / "exports", FixedClock()),
            coordinator=coordinator,
            clock=FixedClock(),
            settings=Settings(
                store_coordinates=False,
                enter_confirmation_seconds=Seconds(120),
                exit_confirmation_seconds=Seconds(180),
                cooldown_seconds=Seconds(300),
                exit_margin_meters=Meters(50),
                database_path=str(tmp_path / "catalog-backend.db"),
            ),
            schedule_export_cleanup=scheduled.append,
            on_event=lambda _occurred_at: None,
        ),
    )
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
        )
    )
    _ = await backend.async_upsert_journal(
        UpsertJournalRequest(resource_id=JOURNAL_ID, name="Presence")
    )
    _ = await backend.async_upsert_rule(
        UpsertRuleRequest(
            resource_id=RULE_ID,
            name="Alice at home",
            tracker_id=TRACKER_ID,
            place_id=PLACE_ID,
            journal_id=JOURNAL_ID,
        )
    )
    mutation_pauses = coordinator.pauses

    listed = await backend.async_list_resources(ListResourcesRequest())
    fetched = await backend.async_get_resource(
        GetResourceRequest(
            resource_type=ResourceType.PLACE,
            resource_id=PLACE_ID,
        )
    )
    with pytest.raises(ResourceInUseError):
        _ = await backend.async_delete_resource(
            DeleteResourceRequest(
                resource_type=ResourceType.TRACKER,
                resource_id=TRACKER_ID,
                confirm=True,
            )
        )
    deleted = await backend.async_delete_resource(
        DeleteResourceRequest(
            resource_type=ResourceType.RULE,
            resource_id=RULE_ID,
            confirm=True,
        )
    )
    await store.async_close()

    assert len(listed.resources) == 4
    assert isinstance(fetched.resource, PlaceResourceItem)
    assert deleted.resource_type is ResourceType.RULE
    assert coordinator.pauses == mutation_pauses + 2
    assert coordinator.resumes == coordinator.pauses
    assert scheduled == []
