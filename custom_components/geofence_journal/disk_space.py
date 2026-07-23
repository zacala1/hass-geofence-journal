"""Conservative disk headroom checks for file-expanding operations."""

from __future__ import annotations

from pathlib import Path
from shutil import disk_usage
from typing import Final, final, override

from .storage.errors import StorageError

MINIMUM_FREE_BYTES: Final = 64 * 1024 * 1024
EXPORT_SIZE_MULTIPLIER: Final = 3
COMPACT_SIZE_MULTIPLIER: Final = 2


@final
class InsufficientDiskSpaceError(StorageError):
    """Available capacity cannot safely complete a requested operation."""

    __slots__ = ("available_bytes", "required_bytes")
    available_bytes: int
    required_bytes: int

    def __init__(self, *, available_bytes: int, required_bytes: int) -> None:
        """Retain capacity numbers without retaining a private path."""
        super().__init__(available_bytes, required_bytes)
        self.available_bytes = available_bytes
        self.required_bytes = required_bytes

    @override
    def __str__(self) -> str:
        """Return an actionable, path-free capacity error."""
        return (
            f"insufficient disk space: {self.available_bytes} bytes available, "
            f"{self.required_bytes} bytes required"
        )


def export_headroom_bytes(database_bytes: int) -> int:
    """Estimate conservative CSV expansion headroom."""
    return max(MINIMUM_FREE_BYTES, database_bytes * EXPORT_SIZE_MULTIPLIER)


def compact_headroom_bytes(database_bytes: int) -> int:
    """Estimate SQLite VACUUM temporary-file headroom."""
    return max(MINIMUM_FREE_BYTES, database_bytes * COMPACT_SIZE_MULTIPLIER)


def require_export_space(database_path: str | Path, export_path: Path) -> None:
    """Require export-filesystem headroom based on database and WAL size."""
    database_path = Path(database_path)
    database_bytes = _file_bytes(database_path) + _file_bytes(
        Path(f"{database_path}-wal")
    )
    require_free_space(
        export_path,
        required_bytes=export_headroom_bytes(database_bytes),
    )


def require_compact_space(database_path: str | Path) -> None:
    """Require database-filesystem headroom before checkpoint and VACUUM."""
    database_path = Path(database_path)
    database_bytes = _file_bytes(database_path) + _file_bytes(
        Path(f"{database_path}-wal")
    )
    require_free_space(
        database_path,
        required_bytes=compact_headroom_bytes(database_bytes),
    )


def require_free_space(target_path: Path, *, required_bytes: int) -> None:
    """Raise before mutation when the target filesystem lacks headroom."""
    available_bytes = _free_bytes(target_path)
    if available_bytes < required_bytes:
        raise InsufficientDiskSpaceError(
            available_bytes=available_bytes,
            required_bytes=required_bytes,
        )


def _free_bytes(target_path: Path) -> int:
    return disk_usage(target_path.parent).free


def _file_bytes(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0
