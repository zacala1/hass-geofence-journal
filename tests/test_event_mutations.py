from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import SQLConnection

from custom_components.geofence_journal.models import (
    EventId,
    JournalId,
    PlaceId,
    TrackerId,
)
from custom_components.geofence_journal.storage.events import (
    AddEventRequest,
    EventMutation,
    MissingEventReferenceError,
    add_event,
    exclude_event,
    restore_event,
)
from custom_components.geofence_journal.storage.repository import SQLiteStore

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
STAMP = "2026-07-18T12:00:00.000000Z"


def _open_seeded(tmp_path: Path, name: str) -> SQLConnection:
    path = tmp_path / name
    with SQLiteStore(path):
        pass
    connection = sqlite3.connect(path, isolation_level=None)
    _ = connection.execute("PRAGMA foreign_keys=ON")
    _ = connection.execute(
        "INSERT INTO trackers VALUES (?,?,?,?,?,?,?)",
        ("tracker-1", "person.fixture", "Fixture", "person", 1, STAMP, STAMP),
    )
    _ = connection.execute(
        """INSERT INTO places
        (id,name,source_type,latitude,longitude,radius_m,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        ("place-1", "Home", "coordinates", 37.0, 127.0, 100.0, STAMP, STAMP),
    )
    _ = connection.execute(
        "INSERT INTO journals (id,name,created_at,updated_at) VALUES (?,?,?,?)",
        ("journal-1", "Journal", STAMP, STAMP),
    )
    return connection


def _event(event_id: str, place_id: str = "place-1") -> AddEventRequest:
    return AddEventRequest(
        event_id=EventId(event_id),
        journal_id=JournalId("journal-1"),
        tracker_id=TrackerId("tracker-1"),
        place_id=PlaceId(place_id),
        occurred_at=NOW,
        confirmed_at=NOW,
        latitude=37.0,
        longitude=127.0,
        accuracy_m=5.0,
        note=None,
    )


def _mutation(event_id: str, reason: str = "review") -> EventMutation:
    return EventMutation(EventId(event_id), NOW, "admin-user", reason)


def test_add_event_inserts_only_a_validated_manual_event(tmp_path: Path) -> None:
    # Given
    connection = _open_seeded(tmp_path, "manual.db")
    with closing(connection):
        # When
        result = add_event(connection, _event("event-1"))

        # Then
        row = connection.execute(
            "SELECT event_type,source,status,rule_id,latitude FROM location_events"
        ).fetchone()
        assert result.event_id == EventId("event-1")
        assert row == ("manual", "manual", "confirmed", None, 37.0)


def test_add_event_rejects_a_missing_reference_without_a_partial_row(
    tmp_path: Path,
) -> None:
    # Given
    connection = _open_seeded(tmp_path, "missing-reference.db")
    with closing(connection):
        # When
        with pytest.raises(MissingEventReferenceError, match="missing-place"):
            _ = add_event(connection, _event("event-1", "missing-place"))

        # Then
        count = connection.execute("SELECT COUNT(*) FROM location_events").fetchone()
        assert count == (0,)


def test_schema_rejects_a_malformed_event_enum_without_a_partial_row(
    tmp_path: Path,
) -> None:
    # Given
    connection = _open_seeded(tmp_path, "malformed-enum.db")
    with closing(connection):
        # When
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """INSERT INTO location_events
                (id,journal_id,tracker_id,place_id,event_type,occurred_at,
                 confirmed_at,source,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    "bad",
                    "journal-1",
                    "tracker-1",
                    "place-1",
                    "stay",
                    STAMP,
                    STAMP,
                    "manual",
                    STAMP,
                    STAMP,
                ),
            )

        # Then
        count = connection.execute("SELECT COUNT(*) FROM location_events").fetchone()
        assert count == (0,)


def test_exclude_event_retains_the_event_and_appends_revision_json(
    tmp_path: Path,
) -> None:
    # Given
    connection = _open_seeded(tmp_path, "exclude.db")
    with closing(connection):
        _ = add_event(connection, _event("event-1"))

        # When
        result = exclude_event(connection, _mutation("event-1", "boundary noise"))

        # Then
        revision = connection.execute(
            "SELECT old_data,new_data,reason,changed_by FROM event_revisions"
        ).fetchone()
        assert result.changed is True
        assert connection.execute("SELECT status FROM location_events").fetchone() == (
            "excluded",
        )
        assert revision is not None
        assert revision[0] == f'{{"changed_at":"{STAMP}","status":"confirmed"}}'
        assert revision[1] == f'{{"changed_at":"{STAMP}","status":"excluded"}}'
        assert revision[2:] == ("boundary noise", "admin-user")


def test_restore_event_appends_the_inverse_revision(tmp_path: Path) -> None:
    # Given
    connection = _open_seeded(tmp_path, "restore.db")
    with closing(connection):
        _ = add_event(connection, _event("event-1"))
        _ = exclude_event(connection, _mutation("event-1"))

        # When
        result = restore_event(connection, _mutation("event-1", "accepted"))

        # Then
        rows = connection.execute(
            "SELECT new_data FROM event_revisions ORDER BY rowid"
        ).fetchall()
        assert result.changed is True
        assert connection.execute("SELECT status FROM location_events").fetchone() == (
            "confirmed",
        )
        assert rows == [
            (f'{{"changed_at":"{STAMP}","status":"excluded"}}',),
            (f'{{"changed_at":"{STAMP}","status":"confirmed"}}',),
        ]


def test_repeated_exclude_is_an_idempotent_no_op(tmp_path: Path) -> None:
    # Given
    connection = _open_seeded(tmp_path, "exclude-repeat.db")
    with closing(connection):
        _ = add_event(connection, _event("event-1"))
        _ = exclude_event(connection, _mutation("event-1"))

        # When
        result = exclude_event(connection, _mutation("event-1"))

        # Then
        assert result.changed is False
        assert result.revision_id is None
        assert connection.execute(
            "SELECT COUNT(*) FROM event_revisions"
        ).fetchone() == (1,)


def test_revision_failure_rolls_back_the_status_change(tmp_path: Path) -> None:
    # Given
    connection = _open_seeded(tmp_path, "revision-rollback.db")
    with closing(connection):
        _ = add_event(connection, _event("event-1"))
        _ = connection.execute(
            """CREATE TRIGGER reject_revision BEFORE INSERT ON event_revisions
            BEGIN SELECT RAISE(ABORT, 'revision fault'); END"""
        )

        # When
        with pytest.raises(sqlite3.IntegrityError, match="revision fault"):
            _ = exclude_event(connection, _mutation("event-1"))

        # Then
        assert connection.execute("SELECT status FROM location_events").fetchone() == (
            "confirmed",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM event_revisions"
        ).fetchone() == (0,)
