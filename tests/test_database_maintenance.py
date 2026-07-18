from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from time import perf_counter
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
    add_event,
    exclude_event,
)
from custom_components.geofence_journal.storage.maintenance import (
    CheckpointBusyError,
    PurgeConfirmationError,
    PurgeRequest,
    ResetConfirmationError,
    ResetRequest,
    compact_database,
    purge_events,
    reset_database,
)
from custom_components.geofence_journal.storage.repository import SQLiteStore

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=2)
STAMP = "2026-07-18T12:00:00.000000Z"
RESET_PHRASE = "DELETE ALL GEOFENCE JOURNAL DATA"


def _connect(path: Path, *, timeout: float = 5.0) -> SQLConnection:
    return sqlite3.connect(path, timeout=timeout, isolation_level=None)


def _open_seeded(tmp_path: Path, name: str) -> tuple[Path, SQLConnection]:
    path = tmp_path / name
    with SQLiteStore(path):
        pass
    connection = _connect(path, timeout=0.05)
    _ = connection.execute("PRAGMA foreign_keys=ON")
    _ = connection.execute("PRAGMA busy_timeout=50")
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
    _ = connection.execute(
        """INSERT INTO recording_rules
        (id,name,tracker_id,place_id,journal_id,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)""",
        ("rule-1", "Rule", "tracker-1", "place-1", "journal-1", STAMP, STAMP),
    )
    return path, connection


def _event(event_id: str, occurred_at: datetime = OLD) -> AddEventRequest:
    return AddEventRequest(
        EventId(event_id),
        JournalId("journal-1"),
        TrackerId("tracker-1"),
        PlaceId("place-1"),
        occurred_at,
        NOW,
        None,
        None,
        None,
        None,
    )


def test_purge_dry_run_counts_without_mutating(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "purge-dry.db")
    with closing(connection):
        _ = add_event(connection, _event("old"))
        _ = exclude_event(connection, EventMutation(EventId("old"), NOW, None, "noise"))

        # When
        result = purge_events(
            connection,
            PurgeRequest(
                before=NOW,
                journal_id=JournalId("journal-1"),
                dry_run=True,
                confirm=False,
            ),
        )

        # Then
        assert (result.matched_events, result.matched_revisions) == (1, 1)
        assert (result.deleted_events, result.deleted_revisions) == (0, 0)
        assert connection.execute(
            "SELECT COUNT(*) FROM location_events"
        ).fetchone() == (1,)


def test_purge_requires_confirmation_for_mutation(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "purge-confirm.db")
    with closing(connection):
        _ = add_event(connection, _event("old"))

        # When
        with pytest.raises(PurgeConfirmationError):
            _ = purge_events(
                connection,
                PurgeRequest(before=NOW, journal_id=None, dry_run=False, confirm=False),
            )

        # Then
        assert connection.execute(
            "SELECT COUNT(*) FROM location_events"
        ).fetchone() == (1,)


def test_confirmed_purge_deletes_fk_dependents_before_events(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "purge.db")
    with closing(connection):
        _ = add_event(connection, _event("old"))
        _ = exclude_event(connection, EventMutation(EventId("old"), NOW, None, "noise"))
        _ = connection.execute(
            """INSERT INTO runtime_states
            (rule_id,presence_state,last_event_id,last_event_type,last_event_at,
             pending_generation,updated_at) VALUES (?,?,?,?,?,?,?)""",
            ("rule-1", "inside", "old", "manual", STAMP, 0, STAMP),
        )

        # When
        result = purge_events(
            connection,
            PurgeRequest(
                before=NOW,
                journal_id=JournalId("journal-1"),
                dry_run=False,
                confirm=True,
            ),
        )

        # Then
        assert (result.deleted_events, result.deleted_revisions) == (1, 1)
        assert connection.execute(
            "SELECT last_event_id FROM runtime_states"
        ).fetchone() == (None,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repeated_purge_is_stable(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "purge-repeat.db")
    request = PurgeRequest(before=NOW, journal_id=None, dry_run=False, confirm=True)
    with closing(connection):
        _ = add_event(connection, _event("old"))
        _ = purge_events(connection, request)

        # When
        result = purge_events(connection, request)

        # Then
        assert (result.matched_events, result.deleted_events) == (0, 0)


def test_purge_failure_restores_revisions_and_events(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "purge-rollback.db")
    with closing(connection):
        _ = add_event(connection, _event("old"))
        _ = exclude_event(connection, EventMutation(EventId("old"), NOW, None, None))
        _ = connection.execute(
            """CREATE TRIGGER reject_event_delete BEFORE DELETE ON location_events
            BEGIN SELECT RAISE(ABORT, 'purge fault'); END"""
        )

        # When
        with pytest.raises(sqlite3.IntegrityError, match="purge fault"):
            _ = purge_events(
                connection,
                PurgeRequest(before=NOW, journal_id=None, dry_run=False, confirm=True),
            )

        # Then
        assert connection.execute(
            "SELECT COUNT(*) FROM location_events"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM event_revisions"
        ).fetchone() == (1,)


def test_locked_purge_respects_the_connection_busy_timeout(tmp_path: Path) -> None:
    # Given
    path, connection = _open_seeded(tmp_path, "locked.db")
    blocker = sqlite3.connect(path, isolation_level=None)
    _ = blocker.execute("BEGIN IMMEDIATE")
    started = perf_counter()
    try:
        # When
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _ = purge_events(
                connection,
                PurgeRequest(before=NOW, journal_id=None, dry_run=False, confirm=True),
            )

        # Then
        assert perf_counter() - started < 0.5
    finally:
        blocker.rollback()
        blocker.close()
        connection.close()


def test_locked_checkpoint_fails_bounded_without_vacuum(tmp_path: Path) -> None:
    # Given
    path, connection = _open_seeded(tmp_path, "checkpoint-locked.db")
    _ = add_event(connection, _event("before-reader"))
    _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    blocker = _connect(path)
    _ = blocker.execute("BEGIN")
    assert blocker.execute("SELECT COUNT(*) FROM location_events").fetchone() == (1,)
    _ = add_event(connection, _event("after-reader"))
    started = perf_counter()
    try:
        # When
        with pytest.raises(CheckpointBusyError):
            _ = compact_database(connection)

        # Then
        assert perf_counter() - started < 0.5
    finally:
        blocker.rollback()
        blocker.close()
        connection.close()


def test_reset_rejects_the_wrong_phrase_without_mutating(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "reset-phrase.db")
    with closing(connection):
        _ = add_event(connection, _event("event-1", NOW))

        # When
        with pytest.raises(ResetConfirmationError):
            _ = reset_database(
                connection, ResetRequest("delete all geofence journal data")
            )

        # Then
        assert connection.execute(
            "SELECT COUNT(*) FROM location_events"
        ).fetchone() == (1,)


def test_reset_clears_domain_rows_and_reinitializes_schema_v1(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "reset.db")
    with closing(connection):
        _ = add_event(connection, _event("event-1", NOW))
        _ = exclude_event(
            connection, EventMutation(EventId("event-1"), NOW, None, "noise")
        )
        _ = connection.execute(
            """INSERT INTO runtime_states
            (rule_id,presence_state,last_event_id,last_event_type,last_event_at,
             pending_generation,updated_at) VALUES (?,?,?,?,?,?,?)""",
            ("rule-1", "inside", "event-1", "manual", STAMP, 0, STAMP),
        )

        # When
        result = reset_database(connection, ResetRequest(RESET_PHRASE))

        # Then
        assert result.deleted_events == 1
        assert result.deleted_revisions == 1
        assert result.deleted_runtime_states == 1
        assert result.deleted_resources == 4
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (
            1,
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repeated_reset_is_stable(tmp_path: Path) -> None:
    # Given
    _, connection = _open_seeded(tmp_path, "reset-repeat.db")
    with closing(connection):
        _ = reset_database(connection, ResetRequest(RESET_PHRASE))

        # When
        result = reset_database(connection, ResetRequest(RESET_PHRASE))

        # Then
        assert result.deleted_events == 0
        assert result.deleted_resources == 0
        assert result.schema_version == 1
