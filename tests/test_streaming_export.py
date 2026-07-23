from __future__ import annotations

from typing import TYPE_CHECKING, final
from uuid import UUID

from custom_components.geofence_journal.export import (
    BASE_HEADERS,
    ExportRequest,
    export_journal_csv,
)

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import (
        SQLiteParameters,
        SQLiteRow,
    )


@final
class StreamingOnlyCursor:
    def __init__(self, rows: list[SQLiteRow]) -> None:
        self._rows = rows
        self._position = 0
        self.fetchone_calls = 0

    def fetchone(self) -> SQLiteRow | None:
        self.fetchone_calls += 1
        if self._position >= len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchall(self) -> list[SQLiteRow]:
        msg = "export must not materialize the complete result"
        raise AssertionError(msg)


@final
class StreamingOnlyConnection:
    def __init__(self, cursor: StreamingOnlyCursor) -> None:
        self._cursor = cursor

    def execute(
        self, sql: str, parameters: SQLiteParameters = (), /
    ) -> StreamingOnlyCursor:
        del sql, parameters
        return self._cursor

    def commit(self) -> None:
        return

    def rollback(self) -> None:
        return

    def close(self) -> None:
        return


def _event_row(event_id: str) -> SQLiteRow:
    return (
        event_id,
        "00000000-0000-4000-8000-000000000001",
        "Presence",
        None,
        "00000000-0000-4000-8000-000000000002",
        "Alice",
        "00000000-0000-4000-8000-000000000003",
        "Home",
        "manual",
        "2026-07-18T12:00:00Z",
        "2026-07-18T12:00:00Z",
        "manual",
        "confirmed",
        "arrived",
        37.5,
        127.0,
        5.0,
    )


def test_csv_export_consumes_cursor_in_bounded_batches(tmp_path: Path) -> None:
    # Given: a cursor that deliberately rejects full-result materialization.
    cursor = StreamingOnlyCursor([_event_row("event-1"), _event_row("event-2")])
    connection = StreamingOnlyConnection(cursor)
    output = tmp_path / "streamed.csv"

    # When: a coordinate-free journal export is generated.
    count = export_journal_csv(
        connection,
        output,
        ExportRequest(journal_id=UUID("00000000-0000-4000-8000-000000000001")),
    )

    # Then: rows were fetched incrementally and the privacy header is unchanged.
    assert count == 2
    assert cursor.fetchone_calls == 3
    assert output.read_text("utf-8-sig").splitlines()[0] == ",".join(BASE_HEADERS)
