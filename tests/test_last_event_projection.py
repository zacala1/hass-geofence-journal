from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final
from uuid import UUID

from custom_components.geofence_journal.entity_state import HealthyEntityState
from custom_components.geofence_journal.export import ExportArtifact, ExportRegistry
from custom_components.geofence_journal.maintenance import AddEventRequest
from custom_components.geofence_journal.management_backend import (
    ManagementBackendDependencies,
    SQLiteManagementBackend,
)
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    EventId,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.events import (
    AddEventRequest as StorageAddEventRequest,
)
from custom_components.geofence_journal.storage.events import add_event

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

NEWER_EVENT_AT: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
OLDER_EVENT_AT: Final = datetime(2026, 7, 17, 12, tzinfo=UTC)
LATER_EVENT_AT: Final = datetime(2026, 7, 19, 11, tzinfo=UTC)
CONFIRMED_AT: Final = datetime(2026, 7, 19, 12, tzinfo=UTC)
TRACKER_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = UUID("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = UUID("00000000-0000-4000-8000-000000000003")


@final
class FixedClock:
    """Return a deterministic confirmation instant for manual events."""

    def utc_now(self) -> datetime:
        return CONFIRMED_AT


def _settings(path: Path) -> Settings:
    return Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )


def _seed_newer_event(path: Path) -> None:
    tracker_id = TrackerId(str(TRACKER_ID))
    place_id = PlaceId(str(PLACE_ID))
    journal_id = JournalId(str(JOURNAL_ID))
    with SQLiteStore(path) as store:
        store.upsert_tracker(
            TrackerDefinition(
                tracker_id=tracker_id,
                entity_id="person.projection",
                kind=TrackerKind.PERSON,
                name="Projection tracker",
                enabled=True,
            ),
            NEWER_EVENT_AT,
        )
        store.upsert_place(
            CoordinatePlace(
                place_id=place_id,
                name="Projection place",
                center=Coordinates(latitude=37.5, longitude=127.0),
                radius_m=Meters(100),
            ),
            NEWER_EVENT_AT,
        )
        store.upsert_journal(
            JournalDefinition(
                journal_id=journal_id,
                name="Projection journal",
                enabled=True,
            ),
            NEWER_EVENT_AT,
        )
        _ = store.run_operation(
            lambda connection: add_event(
                connection,
                StorageAddEventRequest(
                    event_id=EventId("newer-event"),
                    journal_id=journal_id,
                    tracker_id=tracker_id,
                    place_id=place_id,
                    occurred_at=NEWER_EVENT_AT,
                    confirmed_at=NEWER_EVENT_AT,
                    latitude=None,
                    longitude=None,
                    accuracy_m=None,
                    note="newer",
                ),
            )
        )


async def _open_backend(
    hass: HomeAssistant, tmp_path: Path
) -> tuple[GeofenceJournalManager, SQLiteManagementBackend]:
    path = tmp_path / "projection.db"
    _seed_newer_event(path)
    manager = GeofenceJournalManager(hass, _settings(path))
    await manager.async_start()
    scheduled_exports: list[ExportArtifact] = []
    return manager, SQLiteManagementBackend(
        manager.store,
        ManagementBackendDependencies(
            exports=ExportRegistry(tmp_path / "exports", FixedClock()),
            coordinator=manager,
            clock=FixedClock(),
            settings=manager.settings,
            schedule_export_cleanup=scheduled_exports.append,
            on_event=manager.record_event,
        ),
    )


async def test_historical_manual_event_does_not_regress_last_event_projection(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: a manager recovered from a newer event in a real SQLite database.
    manager, backend = await _open_backend(hass, tmp_path)

    try:
        # When: the manual add-event path persists an older historical event.
        response = await backend.async_add_event(
            AddEventRequest(
                journal_id=JOURNAL_ID,
                tracker_id=TRACKER_ID,
                place_id=PLACE_ID,
                occurred_at=OLDER_EVENT_AT,
                note="historical backfill",
            ),
            "admin",
        )
        event_count = await manager.store.async_run_operation(
            lambda connection: connection.execute(
                "SELECT count(*) FROM location_events"
            ).fetchone()
        )
        state = manager.entity_state
    finally:
        await manager.async_stop()

    # Then: SQLite retains both events while the projection remains monotonic.
    assert response.changed
    assert event_count == (2,)
    assert isinstance(state, HealthyEntityState)
    assert state.last_event_at == NEWER_EVENT_AT


async def test_newer_manual_event_advances_last_event_projection(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: a manager recovered from an earlier event in a real SQLite database.
    manager, backend = await _open_backend(hass, tmp_path)

    try:
        # When: the manual add-event path commits a chronologically newer event.
        response = await backend.async_add_event(
            AddEventRequest(
                journal_id=JOURNAL_ID,
                tracker_id=TRACKER_ID,
                place_id=PLACE_ID,
                occurred_at=LATER_EVENT_AT,
                note="new latest event",
            ),
            "admin",
        )
        state = manager.entity_state
    finally:
        await manager.async_stop()

    # Then: the live projection advances to the newly committed event instant.
    assert response.changed
    assert isinstance(state, HealthyEntityState)
    assert state.last_event_at == LATER_EVENT_AT
