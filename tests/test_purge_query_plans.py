from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import SQLConnection

from custom_components.geofence_journal.models import JournalId
from custom_components.geofence_journal.storage.maintenance import (
    PurgeRequest,
    purge_events,
)
from custom_components.geofence_journal.storage.repository import SQLiteStore

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("journal_id", "expected_index"),
    [
        (JournalId("journal-1"), "idx_events_journal_time_id"),
        (None, "idx_events_time_id"),
    ],
)
def test_purge_count_selects_the_bounded_history_index(
    tmp_path: Path,
    journal_id: JournalId | None,
    expected_index: str,
) -> None:
    path = tmp_path / f"purge-plan-{expected_index}.db"
    with SQLiteStore(path):
        pass
    with closing(sqlite3.connect(path, isolation_level=None)) as raw_connection:
        connection = cast("SQLConnection", raw_connection)
        traced: list[str] = []
        raw_connection.set_trace_callback(traced.append)
        try:
            _ = purge_events(
                connection,
                PurgeRequest(
                    before=NOW,
                    journal_id=journal_id,
                    dry_run=True,
                    confirm=False,
                ),
            )
        finally:
            raw_connection.set_trace_callback(None)

        event_count_sql = next(
            statement
            for statement in traced
            if statement.startswith("SELECT COUNT(*) FROM location_events")
        )
        plan = connection.execute(f"EXPLAIN QUERY PLAN {event_count_sql}").fetchall()

    assert any(isinstance(row[3], str) and expected_index in row[3] for row in plan)
