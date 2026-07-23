"""Spreadsheet-safe CSV cell encoding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .storage.db_types import SQLiteValue

SPREADSHEET_FORMULA_PREFIXES: Final = ("=", "+", "-", "@")


def encode_csv_line(values: Sequence[SQLiteValue]) -> str:
    """Encode one RFC-style spreadsheet-safe CSV row."""
    return ",".join(_encode_csv_cell(value) for value in values) + "\r\n"


def _encode_csv_cell(value: SQLiteValue) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(
        SPREADSHEET_FORMULA_PREFIXES
    ):
        text = f"'{value}"
    if any(character in text for character in (",", '"', "\r", "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text
