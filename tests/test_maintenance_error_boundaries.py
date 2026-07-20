from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, final

import pytest
from custom_components.geofence_journal.storage.errors import DatabaseSchemaError
from custom_components.geofence_journal.storage.maintenance import (
    CheckpointBusyError,
    PurgeConfirmationError,
    PurgeRequest,
    ResetRequest,
    compact_database,
    purge_events,
    reset_database,
)

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import (
        SQLiteParameters,
        SQLiteRow,
    )

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
RESET_PHRASE = "DELETE ALL GEOFENCE JOURNAL DATA"


@final
class ScriptedCursor:
    def __init__(self, rows: list[SQLiteRow]) -> None:
        self._rows = rows

    def fetchone(self) -> SQLiteRow | None:
        return None if not self._rows else self._rows[0]

    def fetchall(self) -> list[SQLiteRow]:
        return self._rows


@final
class ScriptedConnection:
    def __init__(self, responses: list[list[SQLiteRow]]) -> None:
        self._responses = responses
        self.rolled_back = False
        self.committed = False

    def execute(self, sql: str, parameters: SQLiteParameters = (), /) -> ScriptedCursor:
        _ = (sql, parameters)
        return ScriptedCursor(self._responses.pop(0))

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def test_purge_confirmation_error_has_actionable_text() -> None:
    assert str(PurgeConfirmationError()) == (
        "permanent event purge requires confirm=True"
    )


def test_compact_rejects_a_missing_main_database_row() -> None:
    connection = ScriptedConnection([[]])

    with pytest.raises(DatabaseSchemaError, match="missing SQLite main database"):
        _ = compact_database(connection)


def test_compact_rejects_a_missing_checkpoint_row(tmp_path: Path) -> None:
    database = tmp_path / "missing-checkpoint.db"
    connection = ScriptedConnection([[(0, "main", str(database))], []])

    with pytest.raises(DatabaseSchemaError, match="missing WAL checkpoint result"):
        _ = compact_database(connection)


def test_compact_rejects_a_missing_post_vacuum_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "missing-final-checkpoint.db"
    connection = ScriptedConnection([[(0, "main", str(database))], [(0, 0, 0)], [], []])

    with pytest.raises(
        DatabaseSchemaError, match="missing post-VACUUM checkpoint result"
    ):
        _ = compact_database(connection)


def test_compact_rejects_a_busy_post_vacuum_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "busy-final-checkpoint.db"
    connection = ScriptedConnection(
        [[(0, "main", str(database))], [(0, 1, 1)], [], [(1, 0, 0)]]
    )

    with pytest.raises(CheckpointBusyError):
        _ = compact_database(connection)


def test_purge_rejects_missing_count_rows() -> None:
    connection = ScriptedConnection([[], []])

    with pytest.raises(DatabaseSchemaError, match="missing purge count result"):
        _ = purge_events(
            connection,
            PurgeRequest(before=NOW, journal_id=None, dry_run=True, confirm=False),
        )


def test_reset_rolls_back_when_count_row_is_missing() -> None:
    connection = ScriptedConnection([[("schema_version",)], [(1,)], [], []])

    with pytest.raises(DatabaseSchemaError, match="missing reset count result"):
        _ = reset_database(connection, ResetRequest(RESET_PHRASE))

    assert connection.rolled_back is True
    assert connection.committed is False
