from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import SQLConnection

from custom_components.geofence_journal.models import (
    EventId,
    JournalId,
    PlaceId,
    TrackerId,
)
from custom_components.geofence_journal.storage.events import AddEventRequest, add_event
from custom_components.geofence_journal.storage.maintenance import (
    PurgeRequest,
    compact_database,
    purge_events,
)
from custom_components.geofence_journal.storage.repository import SQLiteStore

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
STAMP = "2026-07-18T12:00:00.000000Z"


def _open_database(path: Path) -> SQLConnection:
    return sqlite3.connect(path, isolation_level=None)


def test_compact_checkpoints_wal_vacuums_and_reports_real_file_sizes(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "compact.db"
    with SQLiteStore(path):
        pass
    connection = _open_database(path)
    with closing(connection):
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
        for index in range(40):
            _ = add_event(
                connection,
                AddEventRequest(
                    EventId(f"event-{index}"),
                    JournalId("journal-1"),
                    TrackerId("tracker-1"),
                    PlaceId("place-1"),
                    NOW,
                    NOW,
                    None,
                    None,
                    None,
                    "x" * 8192,
                ),
            )
        _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        _ = purge_events(
            connection,
            PurgeRequest(
                before=NOW + timedelta(days=1),
                journal_id=None,
                dry_run=False,
                confirm=True,
            ),
        )
        _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()

        # When
        result = compact_database(connection)

        # Then
        version = connection.execute("SELECT version FROM schema_version").fetchone()
        summary = (
            f"events=0 before={result.database_bytes_before} "
            f"after={result.database_bytes_after} "
            f"version={version[0] if version else 0}"
        )
        _ = sys.stdout.write(f"{summary}\n")
        assert result.checkpoint_busy is False
        assert result.database_bytes_after < result.database_bytes_before
        assert path.stat().st_size == result.database_bytes_after
        assert version == (1,)
