"""Strict structural types around sqlite3's untyped row boundary."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .errors import DatabaseSchemaError

type SQLiteValue = str | int | float | bytes | None
type SQLiteParameters = Sequence[SQLiteValue]
type SQLiteRow = tuple[SQLiteValue, ...]


class SQLCursor(Protocol):
    """Typed subset of sqlite3.Cursor used by the repository."""

    def fetchone(self) -> SQLiteRow | None:
        """Return one typed SQLite row."""
        ...

    def fetchall(self) -> list[SQLiteRow]:
        """Return all typed SQLite rows."""
        ...


class SQLConnection(Protocol):
    """Typed subset of sqlite3.Connection used by the repository."""

    def execute(self, sql: str, parameters: SQLiteParameters = (), /) -> SQLCursor:
        """Execute SQL with typed scalar parameters."""
        ...

    def commit(self) -> None:
        """Commit the active transaction."""
        ...

    def rollback(self) -> None:
        """Roll back the active transaction."""
        ...

    def close(self) -> None:
        """Close the database connection."""
        ...


def required_text(value: SQLiteValue, *, field: str) -> str:
    """Parse a required SQLite TEXT result."""
    match value:
        case str() as text:
            return text
        case int() | float() | bytes() | None:
            raise DatabaseSchemaError(detail=f"{field} must be TEXT")


def required_integer(value: SQLiteValue, *, field: str) -> int:
    """Parse a required SQLite INTEGER result."""
    match value:
        case int() as integer:
            return integer
        case str() | float() | bytes() | None:
            raise DatabaseSchemaError(detail=f"{field} must be INTEGER")
