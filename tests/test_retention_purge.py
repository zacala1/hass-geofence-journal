from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import UUID

import pytest
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    EventId,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.retention import (
    PurgeRetentionRequest,
    RetentionNotConfiguredError,
    purge_configured_retention,
)
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.events import (
    AddEventRequest,
    add_event,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW: Final = datetime(2026, 7, 23, 12, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")


def _seed(store: SQLiteStore, *, retention_days: int | None) -> None:
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
            center=Coordinates(0, 0),
            radius_m=Meters(100),
        ),
        NOW,
    )
    store.upsert_journal(
        JournalDefinition(
            journal_id=JOURNAL_ID,
            name="Presence",
            enabled=True,
            retention_days=retention_days,
        ),
        NOW,
    )


def _add_manual_event(store: SQLiteStore, event_id: str, occurred_at: datetime) -> None:
    _ = store.run_operation(
        lambda connection: add_event(
            connection,
            AddEventRequest(
                event_id=EventId(event_id),
                journal_id=JOURNAL_ID,
                tracker_id=TRACKER_ID,
                place_id=PLACE_ID,
                occurred_at=occurred_at,
                confirmed_at=occurred_at,
                latitude=None,
                longitude=None,
                accuracy_m=None,
                note=None,
            ),
        )
    )


def test_retention_purge_is_explicit_and_uses_the_configured_cutoff(
    tmp_path: Path,
) -> None:
    with SQLiteStore(tmp_path / "retention.db") as store:
        _seed(store, retention_days=30)
        _add_manual_event(
            store,
            "00000000-0000-4000-8000-000000000010",
            NOW - timedelta(days=31),
        )
        _add_manual_event(
            store,
            "00000000-0000-4000-8000-000000000011",
            NOW - timedelta(days=29),
        )
        dry_run = store.run_operation(
            lambda connection: purge_configured_retention(
                connection,
                NOW,
                PurgeRetentionRequest(
                    journal_id=UUID(str(JOURNAL_ID)),
                    dry_run=True,
                    confirm=False,
                ),
            )
        )
        deleted = store.run_operation(
            lambda connection: purge_configured_retention(
                connection,
                NOW,
                PurgeRetentionRequest(
                    journal_id=UUID(str(JOURNAL_ID)),
                    dry_run=False,
                    confirm=True,
                ),
            )
        )
        remaining = store.event_count()

    assert dry_run.matched_events == 1
    assert dry_run.deleted_events == 0
    assert deleted.deleted_events == 1
    assert remaining == 1


def test_retention_purge_rejects_a_journal_without_a_policy(tmp_path: Path) -> None:
    with SQLiteStore(tmp_path / "no-retention.db") as store:
        _seed(store, retention_days=None)
        with pytest.raises(RetentionNotConfiguredError):
            _ = store.run_operation(
                lambda connection: purge_configured_retention(
                    connection,
                    NOW,
                    PurgeRetentionRequest(
                        journal_id=UUID(str(JOURNAL_ID)),
                    ),
                )
            )
