"""Independent, query-only SQLite snapshots for long-running reads."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .db_types import SQLConnection


def run_read_operation[T](
    path: Path,
    busy_timeout_ms: int,
    operation: Callable[[SQLConnection], T],
) -> T:
    """Run one query-only operation on an independent WAL snapshot."""
    with closing(
        sqlite3.connect(
            path,
            timeout=busy_timeout_ms / 1_000,
            isolation_level=None,
            check_same_thread=False,
        )
    ) as connection:
        _ = connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        _ = connection.execute("PRAGMA query_only=ON")
        _ = connection.execute("BEGIN")
        try:
            return operation(connection)
        finally:
            connection.rollback()
