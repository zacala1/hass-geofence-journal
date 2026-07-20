from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final
from uuid import UUID

import pytest
from custom_components.geofence_journal.export import ExportRegistry
from custom_components.geofence_journal.maintenance import (
    UpsertPlaceRequest,
    UpsertRuleRequest,
)
from custom_components.geofence_journal.management_backend import (
    ManagementBackendDependencies,
    SQLiteManagementBackend,
)
from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    PlaceKind,
    PresenceState,
    RuleDefinition,
    RuleId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from homeassistant.core import HomeAssistant

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")
RULE_ID: Final = RuleId("00000000-0000-4000-8000-000000000004")


def _seed_coordinate_rule(path: Path) -> None:
    with SQLiteStore(path) as store:
        store.upsert_tracker(
            TrackerDefinition(
                tracker_id=TRACKER_ID,
                entity_id="person.alice",
                kind=TrackerKind.PERSON,
                name="Alice",
                enabled=True,
            ),
            NOW,
        )
        store.upsert_place(
            CoordinatePlace(
                place_id=PLACE_ID,
                name="Old center",
                center=Coordinates(0.0, 0.0),
                radius_m=Meters(100),
            ),
            NOW,
        )
        store.upsert_journal(JournalDefinition(JOURNAL_ID, "Visits", enabled=True), NOW)
        store.upsert_rule(
            RuleDefinition(
                rule_id=RULE_ID,
                tracker_id=TRACKER_ID,
                place_id=PLACE_ID,
                journal_id=JOURNAL_ID,
                enabled=True,
                enter_confirmation_seconds=Seconds(0),
                exit_confirmation_seconds=Seconds(0),
                cooldown_seconds=Seconds(0),
                exit_margin_meters=Meters(50),
                max_gps_accuracy_meters=Meters(100),
            ),
            NOW,
        )


@final
class MoveDuringResumeCoordinator:
    """Inject a tracker update after the write but before listener rebuilding."""

    def __init__(self, manager: GeofenceJournalManager, hass: HomeAssistant) -> None:
        self._manager = manager
        self._hass = hass
        self.pauses = 0

    @asynccontextmanager
    async def pause_and_drain(self) -> AsyncGenerator[None]:
        self.pauses += 1
        async with self._manager.pause_and_drain():
            yield
            self._hass.states.async_set(
                "person.alice",
                "old-center",
                {
                    ATTR_LATITUDE: 0.0,
                    ATTR_LONGITUDE: 0.0,
                    ATTR_GPS_ACCURACY: 5,
                },
            )
            await self._hass.async_block_till_done()


async def test_resource_write_and_listener_rebuild_share_one_pause_scope(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """A tracker callback cannot observe a newly written place through old resources."""
    path = tmp_path / "resource-refresh-race.db"
    _seed_coordinate_rule(path)
    hass.states.async_set(
        "person.alice",
        "away",
        {ATTR_LATITUDE: 0.01, ATTR_LONGITUDE: 0.0, ATTR_GPS_ACCURACY: 5},
    )
    settings = Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )
    manager = GeofenceJournalManager(hass, settings)
    await manager.async_start()
    coordinator = MoveDuringResumeCoordinator(manager, hass)
    backend = SQLiteManagementBackend(
        manager.store,
        ManagementBackendDependencies(
            exports=ExportRegistry(tmp_path / "exports", manager.clock),
            coordinator=coordinator,
            clock=manager.clock,
            settings=settings,
            schedule_export_cleanup=lambda _artifact: None,
            on_event=manager.record_event,
        ),
    )
    try:
        _ = await backend.async_upsert_place(
            UpsertPlaceRequest(
                resource_id=UUID(str(PLACE_ID)),
                name="New center",
                source_type=PlaceKind.COORDINATE,
                latitude=0.02,
                longitude=0.0,
                radius_meters=100,
            )
        )
    finally:
        await manager.async_stop()

    with SQLiteStore(path) as store:
        state = store.runtime_state(str(RULE_ID))
        assert state is not None
        assert coordinator.pauses == 1
        assert state.presence_state is PresenceState.OUTSIDE
        assert store.event_count() == 0


async def test_service_disable_deactivates_removed_rule_runtime_state(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    path = tmp_path / "service-disable.db"
    _seed_coordinate_rule(path)
    hass.states.async_set(
        "person.alice",
        "away",
        {ATTR_LATITUDE: 0.01, ATTR_LONGITUDE: 0.0, ATTR_GPS_ACCURACY: 5},
    )
    settings = Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )
    manager = GeofenceJournalManager(hass, settings)
    await manager.async_start()
    backend = SQLiteManagementBackend(
        manager.store,
        ManagementBackendDependencies(
            exports=ExportRegistry(tmp_path / "exports", manager.clock),
            coordinator=manager,
            clock=manager.clock,
            settings=settings,
            schedule_export_cleanup=lambda _artifact: None,
            on_event=manager.record_event,
        ),
    )
    assert await manager.store.async_runtime_state(str(RULE_ID)) is not None

    try:
        _ = await backend.async_upsert_rule(
            UpsertRuleRequest(
                resource_id=UUID(str(RULE_ID)),
                name="Disabled",
                tracker_id=UUID(str(TRACKER_ID)),
                place_id=UUID(str(PLACE_ID)),
                journal_id=UUID(str(JOURNAL_ID)),
                enabled=False,
            )
        )

        assert manager.listener_entity_ids == ()
        assert await manager.store.async_runtime_state(str(RULE_ID)) is None
    finally:
        await manager.async_stop()


async def test_failed_service_disable_cleanup_is_retried(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    path = tmp_path / "service-disable-retry.db"
    _seed_coordinate_rule(path)
    hass.states.async_set(
        "person.alice",
        "away",
        {ATTR_LATITUDE: 0.01, ATTR_LONGITUDE: 0.0, ATTR_GPS_ACCURACY: 5},
    )
    settings = Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )
    manager = GeofenceJournalManager(hass, settings)
    await manager.async_start()
    backend = SQLiteManagementBackend(
        manager.store,
        ManagementBackendDependencies(
            exports=ExportRegistry(tmp_path / "exports", manager.clock),
            coordinator=manager,
            clock=manager.clock,
            settings=settings,
            schedule_export_cleanup=lambda _artifact: None,
            on_event=manager.record_event,
        ),
    )
    request = UpsertRuleRequest(
        resource_id=UUID(str(RULE_ID)),
        name="Disabled",
        tracker_id=UUID(str(TRACKER_ID)),
        place_id=UUID(str(PLACE_ID)),
        journal_id=UUID(str(JOURNAL_ID)),
        enabled=False,
    )
    _ = await manager.store.async_run_operation(
        lambda connection: connection.execute(
            """CREATE TRIGGER reject_runtime_delete
            BEFORE DELETE ON runtime_states
            BEGIN SELECT RAISE(ABORT, 'delete fault'); END"""
        )
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="delete fault"):
            _ = await backend.async_upsert_rule(request)

        assert manager.listener_entity_ids == ()
        assert await manager.store.async_runtime_state(str(RULE_ID)) is not None
        _ = await manager.store.async_run_operation(
            lambda connection: connection.execute("DROP TRIGGER reject_runtime_delete")
        )

        _ = await backend.async_upsert_rule(request)

        assert manager.listener_entity_ids == ()
        assert await manager.store.async_runtime_state(str(RULE_ID)) is None
    finally:
        await manager.async_stop()
