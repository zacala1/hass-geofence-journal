from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, final
from uuid import UUID

import pytest
from custom_components.geofence_journal import management_backend
from custom_components.geofence_journal.disk_space import InsufficientDiskSpaceError
from custom_components.geofence_journal.export import (
    ExportArtifact,
    ExportRegistry,
    ExportRequest,
)
from custom_components.geofence_journal.maintenance import (
    AddEventRequest,
    ExcludeEventRequest,
    PurgeEventsRequest,
    RestoreEventRequest,
    UpsertJournalRequest,
    UpsertPlaceRequest,
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
from custom_components.geofence_journal.retention import PurgeRetentionRequest
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import AsyncSQLiteStore
from custom_components.geofence_journal.storage.events import MissingEventReferenceError
from pydantic_core import PydanticCustomError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import SQLConnection

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
TRACKER_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = UUID("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = UUID("00000000-0000-4000-8000-000000000003")
MISSING_ID: Final = UUID("00000000-0000-4000-8000-000000000099")


@final
class FixedClock:
    def utc_now(self) -> datetime:
        return NOW


@final
class PauseRecorder:
    def __init__(self) -> None:
        self.pauses = 0

    @asynccontextmanager
    async def pause_and_drain(self) -> AsyncGenerator[None]:
        self.pauses += 1
        yield


async def _open_backend(
    tmp_path: Path,
) -> tuple[
    SQLiteManagementBackend,
    AsyncSQLiteStore,
    ExportRegistry,
    PauseRecorder,
    list[ExportArtifact],
    list[datetime],
]:
    store = AsyncSQLiteStore(tmp_path / "management.db")
    await store.async_open()
    exports = ExportRegistry(tmp_path / "exports", FixedClock())
    coordinator = PauseRecorder()
    scheduled: list[ExportArtifact] = []
    observed_events: list[datetime] = []

    backend = SQLiteManagementBackend(
        store,
        ManagementBackendDependencies(
            exports=exports,
            coordinator=coordinator,
            clock=FixedClock(),
            settings=Settings(
                store_coordinates=True,
                enter_confirmation_seconds=Seconds(120),
                exit_confirmation_seconds=Seconds(180),
                cooldown_seconds=Seconds(300),
                exit_margin_meters=Meters(50),
                database_path=str(tmp_path / "management.db"),
            ),
            schedule_export_cleanup=scheduled.append,
            on_event=observed_events.append,
        ),
    )
    return backend, store, exports, coordinator, scheduled, observed_events


async def _seed(backend: SQLiteManagementBackend) -> None:
    _ = await backend.async_upsert_tracker(
        UpsertTrackerRequest(
            resource_id=TRACKER_ID,
            entity_id="person.coverage",
            kind=TrackerKind.PERSON,
            name="Coverage user",
        )
    )
    _ = await backend.async_upsert_place(
        UpsertPlaceRequest(
            resource_id=PLACE_ID,
            name="Coverage home",
            source_type=PlaceKind.COORDINATE,
            latitude=37.5,
            longitude=127.0,
            radius_meters=100,
        )
    )
    _ = await backend.async_upsert_journal(
        UpsertJournalRequest(
            resource_id=JOURNAL_ID,
            name="Coverage journal",
            retention_days=30,
        )
    )


async def test_manual_mutations_export_purges_and_compaction(tmp_path: Path) -> None:
    backend, store, _, coordinator, scheduled, observed = await _open_backend(tmp_path)
    await _seed(backend)

    added = await backend.async_add_event(
        AddEventRequest(
            journal_id=JOURNAL_ID,
            tracker_id=TRACKER_ID,
            place_id=PLACE_ID,
            occurred_at=NOW,
            latitude=37.5,
            longitude=127.0,
            accuracy_m=5,
            note="manual",
        ),
        "admin",
    )
    event_id = UUID(added.payload.event_id)
    excluded = await backend.async_exclude_event(
        ExcludeEventRequest(event_id=event_id, reason="noise"), "admin"
    )
    restored = await backend.async_restore_event(
        RestoreEventRequest(event_id=event_id, reason="valid"), "admin"
    )
    exported = await backend.async_export_journal(
        ExportRequest(journal_id=JOURNAL_ID, include_coordinates=True)
    )
    dry_run = await backend.async_purge_events(
        PurgeEventsRequest(
            before=NOW + timedelta(seconds=1),
            journal_id=JOURNAL_ID,
        )
    )
    retention_dry_run = await backend.async_purge_retention(
        PurgeRetentionRequest(journal_id=JOURNAL_ID)
    )
    retention_delete = await backend.async_purge_retention(
        PurgeRetentionRequest(
            journal_id=JOURNAL_ID,
            dry_run=False,
            confirm=True,
        )
    )
    compacted = await backend.async_compact_database()
    coordinate_row = await store.async_run_operation(
        lambda connection: connection.execute(
            "SELECT latitude,longitude,accuracy_m FROM location_events"
        ).fetchone()
    )
    await store.async_close()

    assert added.changed
    assert excluded.changed
    assert restored.changed
    assert excluded.payload.status == "excluded"
    assert restored.payload.status == "confirmed"
    assert coordinate_row == (37.5, 127.0, 5.0)
    assert exported.count == 1
    assert len(scheduled) == 1
    assert dry_run.matched_events == 1
    assert dry_run.dry_run
    assert retention_dry_run.dry_run
    assert retention_delete.deleted_events == 0
    assert compacted.database_bytes_after > 0
    assert coordinator.pauses == 5
    assert observed == [NOW]


async def test_invalid_invariants_missing_rows_and_failed_export_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, store, exports, _, scheduled, observed = await _open_backend(tmp_path)
    await _seed(backend)
    invalid_coordinate = UpsertPlaceRequest.model_construct(
        name="Invalid coordinate",
        source_type=PlaceKind.COORDINATE,
        latitude=None,
        longitude=None,
        radius_meters=None,
    )
    invalid_zone = UpsertPlaceRequest.model_construct(
        name="Invalid zone",
        source_type=PlaceKind.HA_ZONE,
        zone_entity_id=None,
    )
    with pytest.raises(PydanticCustomError):
        _ = await backend.async_upsert_place(invalid_coordinate)
    with pytest.raises(PydanticCustomError):
        _ = await backend.async_upsert_place(invalid_zone)
    with pytest.raises(MissingEventReferenceError):
        _ = await backend.async_export_journal(ExportRequest(journal_id=MISSING_ID))

    def fail_export(
        _connection: SQLConnection, _path: Path, _request: ExportRequest
    ) -> int:
        detail = "export failed"
        raise OSError(detail)

    monkeypatch.setattr(management_backend, "export_journal_csv", fail_export)
    with pytest.raises(OSError, match="export failed"):
        _ = await backend.async_export_journal(ExportRequest(journal_id=JOURNAL_ID))
    await store.async_close()

    assert scheduled == []
    assert observed == []
    assert exports.cleanup_orphaned_files() == 0


async def test_disk_preflight_cleans_export_and_avoids_compaction_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, store, exports, coordinator, scheduled, _observed = await _open_backend(
        tmp_path
    )
    await _seed(backend)
    pauses_before = coordinator.pauses

    def fail_space(*_args: object) -> None:
        raise InsufficientDiskSpaceError(
            available_bytes=100,
            required_bytes=200,
        )

    monkeypatch.setattr(management_backend, "require_export_space", fail_space)
    with pytest.raises(InsufficientDiskSpaceError):
        _ = await backend.async_export_journal(ExportRequest(journal_id=JOURNAL_ID))
    monkeypatch.setattr(management_backend, "require_compact_space", fail_space)
    with pytest.raises(InsufficientDiskSpaceError):
        _ = await backend.async_compact_database()
    await store.async_close()

    assert coordinator.pauses == pauses_before
    assert scheduled == []
    assert exports.cleanup_orphaned_files() == 0
