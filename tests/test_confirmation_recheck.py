from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from custom_components.geofence_journal.manager import GeofenceJournalManager
from custom_components.geofence_journal.models import (
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
    ZonePlace,
)
from custom_components.geofence_journal.settings import Settings
from custom_components.geofence_journal.storage import SQLiteStore
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from tests.test_runtime_fixtures import RecoveryClock, RecoveryScheduler

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")
RULE_ID: Final = RuleId("00000000-0000-4000-8000-000000000004")


def _seed_zone_rule(path: Path) -> None:
    with SQLiteStore(path) as store:
        store.upsert_tracker(
            TrackerDefinition(
                TRACKER_ID,
                "person.alice",
                TrackerKind.PERSON,
                "Alice",
                enabled=True,
            ),
            NOW,
        )
        store.upsert_place(
            ZonePlace(PLACE_ID, "Office", "zone.office"),
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
                enter_confirmation_seconds=Seconds(120),
                exit_confirmation_seconds=Seconds(0),
                cooldown_seconds=Seconds(0),
                exit_margin_meters=Meters(50),
                max_gps_accuracy_meters=Meters(100),
            ),
            NOW,
        )


async def test_pending_deadline_rechecks_live_zone_geometry(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: an outside baseline followed by a pending enter observation.
    path = tmp_path / "deadline-zone.db"
    _seed_zone_rule(path)
    hass.states.async_set(
        "zone.office",
        "0",
        {ATTR_LATITUDE: 0.0, ATTR_LONGITUDE: 0.0, "radius": 100},
    )
    hass.states.async_set(
        "person.alice",
        "away",
        {ATTR_LATITUDE: 0.01, ATTR_LONGITUDE: 0.0, ATTR_GPS_ACCURACY: 5},
    )
    clock = RecoveryClock(NOW)
    scheduler = RecoveryScheduler(clock)
    manager = GeofenceJournalManager(
        hass,
        Settings(
            store_coordinates=False,
            enter_confirmation_seconds=Seconds(120),
            exit_confirmation_seconds=Seconds(0),
            cooldown_seconds=Seconds(0),
            exit_margin_meters=Meters(50),
            database_path=str(path),
        ),
        clock=clock,
        scheduler=scheduler,
    )
    await manager.async_start()
    try:
        hass.states.async_set(
            "person.alice",
            "office",
            {ATTR_LATITUDE: 0.0, ATTR_LONGITUDE: 0.0, ATTR_GPS_ACCURACY: 5},
        )
        await hass.async_block_till_done()

        # When: only the live Zone moves away before confirmation becomes due.
        hass.states.async_set(
            "zone.office",
            "0",
            {ATTR_LATITUDE: 0.02, ATTR_LONGITUDE: 0.0, "radius": 100},
        )
        await scheduler.advance(121)
    finally:
        await manager.async_stop()

    # Then: the fresh geometry cancels the candidate instead of creating an enter.
    with SQLiteStore(path) as store:
        state = store.runtime_state(str(RULE_ID))
        assert state is not None
        assert state.pending_transition is None
        assert store.event_count() == 0
