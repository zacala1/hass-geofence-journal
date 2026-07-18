from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID

import anyio
from custom_components.geofence_journal.const import DOMAIN
from custom_components.geofence_journal.export import ExportArtifact, ExportRegistry
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
    RuleDefinition,
    RuleId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.services import (
    async_register_services,
    async_unregister_services,
)
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from homeassistant.core import Context

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockUser

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")
RULE_ID: Final = RuleId("00000000-0000-4000-8000-000000000004")
NEW_JOURNAL_ID: Final = UUID("00000000-0000-4000-8000-000000000005")
RESET_PHRASE: Final = "DELETE ALL GEOFENCE JOURNAL DATA"


def _settings(path: Path) -> Settings:
    return Settings(
        store_coordinates=False,
        enter_confirmation_seconds=Seconds(0),
        exit_confirmation_seconds=Seconds(0),
        cooldown_seconds=Seconds(0),
        exit_margin_meters=Meters(50),
        database_path=str(path),
    )


def _seed_runnable_rule(path: Path) -> None:
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
                name="Home",
                center=Coordinates(latitude=37.5, longitude=127.0),
                radius_m=Meters(100),
            ),
            NOW,
        )
        store.upsert_journal(
            JournalDefinition(journal_id=JOURNAL_ID, name="Presence", enabled=True),
            NOW,
        )
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


def _ignore_export_cleanup(artifact: ExportArtifact) -> None:
    _ = artifact


async def test_real_manager_reset_resumes_empty_and_services_remain_reusable(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "reset-integration.db"
    _seed_runnable_rule(database_path)
    manager = GeofenceJournalManager(hass, _settings(database_path))
    await manager.async_start()
    registry = ExportRegistry(tmp_path / "exports", manager.clock)
    artifact = registry.allocate()
    _ = artifact.path.write_bytes(b"journal-derived")
    backend = SQLiteManagementBackend(
        manager.store,
        ManagementBackendDependencies(
            exports=registry,
            coordinator=manager,
            clock=manager.clock,
            settings=manager.settings,
            schedule_export_cleanup=_ignore_export_cleanup,
            on_event=manager.record_event,
        ),
    )
    await async_register_services(hass, backend)

    try:
        assert manager.listener_entity_ids == ("person.alice",)
        with anyio.fail_after(5):
            reset_response = await hass.services.async_call(
                DOMAIN,
                "reset_database",
                {"confirmation": RESET_PHRASE},
                blocking=True,
                context=Context(user_id=hass_admin_user.id),
                return_response=True,
            )

        assert reset_response is not None
        assert reset_response["schema_version"] == 1
        assert manager.listener_entity_ids == ()
        assert registry.resolve(artifact.export_id) is None
        assert not artifact.path.exists()

        with anyio.fail_after(5):
            upsert_response = await hass.services.async_call(
                DOMAIN,
                "upsert_journal",
                {"resource_id": str(NEW_JOURNAL_ID), "name": "After reset"},
                blocking=True,
                context=Context(user_id=hass_admin_user.id),
                return_response=True,
            )
        assert upsert_response == {"resource_id": str(NEW_JOURNAL_ID)}
        assert manager.listener_entity_ids == ()
    finally:
        await async_unregister_services(hass)
        await manager.async_stop()
