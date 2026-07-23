"""Backward-compatible query indexes for schema v1 databases."""

from __future__ import annotations

import sqlite3
from typing import Final

from .db_types import SQLConnection, required_text
from .errors import DatabaseSchemaError

type IndexContract = tuple[str, str, tuple[str, ...]]

ADDITIVE_V1_INDEXES: Final[tuple[IndexContract, ...]] = (
    (
        "idx_events_time_id",
        """CREATE INDEX IF NOT EXISTS idx_events_time_id
        ON location_events(occurred_at,id)""",
        ("occurred_at", "id"),
    ),
    (
        "idx_events_journal_time_id",
        """CREATE INDEX IF NOT EXISTS idx_events_journal_time_id
        ON location_events(journal_id,occurred_at,id)""",
        ("journal_id", "occurred_at", "id"),
    ),
    (
        "idx_revisions_event",
        "CREATE INDEX IF NOT EXISTS idx_revisions_event ON event_revisions(event_id)",
        ("event_id",),
    ),
)


def ensure_additive_v1_indexes(connection: SQLConnection) -> None:
    """Atomically install and validate indexes safe for every v1 database."""
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        for name, statement, expected_columns in ADDITIVE_V1_INDEXES:
            _ = connection.execute(statement)
            rows = connection.execute(f"PRAGMA index_info('{name}')").fetchall()
            columns = tuple(
                required_text(row[2], field=f"{name} column") for row in rows
            )
            _validate_columns(name, columns, expected_columns)
    except sqlite3.Error, DatabaseSchemaError:
        connection.rollback()
        raise
    connection.commit()


def _validate_columns(
    name: str, columns: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    if columns != expected:
        raise DatabaseSchemaError(detail=f"index {name} has unexpected columns")
