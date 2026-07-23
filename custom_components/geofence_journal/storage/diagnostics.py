"""Privacy-neutral operational diagnostics read from SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .db_types import SQLiteRow, required_integer, required_text
from .errors import DatabaseSchemaError

if TYPE_CHECKING:
    from .db_types import SQLConnection


@dataclass(frozen=True, slots=True)
class StorageDiagnosticSnapshot:
    """Counts and invariants that cannot identify people or places."""

    schema_version: int
    journal_mode: str
    foreign_keys_enabled: bool
    quick_check_ok: bool
    tracker_count: int
    place_count: int
    journal_count: int
    rule_count: int
    active_rule_count: int
    event_count: int
    revision_count: int
    runtime_state_count: int


def collect_storage_diagnostics(
    connection: SQLConnection,
) -> StorageDiagnosticSnapshot:
    """Read one consistent, identifier-free database snapshot."""
    schema_row = _required_row(
        connection.execute("SELECT version FROM schema_version").fetchone(),
        field="schema version",
    )
    journal_row = _required_row(
        connection.execute("PRAGMA journal_mode").fetchone(),
        field="journal mode",
    )
    foreign_keys_row = _required_row(
        connection.execute("PRAGMA foreign_keys").fetchone(),
        field="foreign keys",
    )
    quick_check_row = _required_row(
        connection.execute("PRAGMA quick_check(1)").fetchone(),
        field="quick check",
    )
    counts_row = _required_row(
        connection.execute(
            """SELECT
        (SELECT COUNT(*) FROM trackers),
        (SELECT COUNT(*) FROM places),
        (SELECT COUNT(*) FROM journals),
        (SELECT COUNT(*) FROM recording_rules),
        (SELECT COUNT(*) FROM recording_rules WHERE enabled=1),
        (SELECT COUNT(*) FROM location_events),
        (SELECT COUNT(*) FROM event_revisions),
        (SELECT COUNT(*) FROM runtime_states)"""
        ).fetchone(),
        field="resource counts",
    )
    return StorageDiagnosticSnapshot(
        schema_version=required_integer(schema_row[0], field="schema version"),
        journal_mode=required_text(journal_row[0], field="journal mode"),
        foreign_keys_enabled=(
            required_integer(foreign_keys_row[0], field="foreign keys") == 1
        ),
        quick_check_ok=required_text(quick_check_row[0], field="quick check") == "ok",
        tracker_count=required_integer(counts_row[0], field="tracker count"),
        place_count=required_integer(counts_row[1], field="place count"),
        journal_count=required_integer(counts_row[2], field="journal count"),
        rule_count=required_integer(counts_row[3], field="rule count"),
        active_rule_count=required_integer(counts_row[4], field="active rule count"),
        event_count=required_integer(counts_row[5], field="event count"),
        revision_count=required_integer(counts_row[6], field="revision count"),
        runtime_state_count=required_integer(
            counts_row[7], field="runtime state count"
        ),
    )


def _required_row(row: SQLiteRow | None, *, field: str) -> SQLiteRow:
    if row is None:
        raise DatabaseSchemaError(detail=f"diagnostic {field} returned no row")
    return row
