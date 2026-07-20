"""Open export artifacts without following link substitutions."""

from __future__ import annotations

import os
from stat import S_ISREG
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path
    from typing import BinaryIO


def is_regular_file_without_links(path: Path) -> bool:
    """Return true only for a directly addressed regular file."""
    try:
        return S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def open_verified_regular_file(path: Path) -> BinaryIO | None:
    """Open one stable regular-file descriptor without following symlinks."""
    before = _regular_stat(path)
    if before is None:
        return None
    descriptor = _open_descriptor(path)
    if descriptor is None:
        return None
    if not _descriptor_matches(descriptor, before):
        os.close(descriptor)
        return None
    return _binary_stream(descriptor)


def _regular_stat(path: Path) -> os.stat_result | None:
    try:
        result = path.lstat()
    except OSError:
        return None
    return result if S_ISREG(result.st_mode) else None


def _open_descriptor(path: Path) -> int | None:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags)
    except OSError:
        return None


def _descriptor_matches(descriptor: int, before: os.stat_result) -> bool:
    try:
        after = os.fstat(descriptor)
    except OSError:
        return False
    return S_ISREG(after.st_mode) and os.path.samestat(before, after)


def _binary_stream(descriptor: int) -> BinaryIO | None:
    try:
        return cast("BinaryIO", os.fdopen(descriptor, "rb"))
    except OSError:
        os.close(descriptor)
        return None
