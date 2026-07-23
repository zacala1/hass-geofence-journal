"""Validate the explicit runtime-source contract for release archives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from scripts.release_errors import (
    ReleaseDirtyTreeError,
    ReleaseSymlinkError,
    ReleaseUnexpectedSourceError,
)
from scripts.release_repository import tracked_integration_files

if TYPE_CHECKING:
    from pathlib import Path

IGNORED_CACHE_SUFFIXES: Final = frozenset({".pyc", ".pyo"})
TRANSLATION_PATH_PARTS: Final = 2
BRAND_FILENAMES: Final = frozenset(
    {
        "dark_icon.png",
        "dark_icon@2x.png",
        "dark_logo.png",
        "dark_logo@2x.png",
        "icon.png",
        "icon@2x.png",
        "logo.png",
        "logo@2x.png",
    }
)


def validated_release_sources(root: Path, integration: Path) -> tuple[Path, ...]:
    """Return tracked, allowlisted runtime files in deterministic order."""
    tracked = tracked_integration_files(root, integration)
    sources: list[Path] = []
    for source in sorted(integration.rglob("*")):
        if source.is_symlink():
            raise ReleaseSymlinkError(source)
        if not source.is_file() or _is_ignored_cache(source, integration):
            continue
        relative = source.relative_to(integration)
        if not _is_runtime_source(relative):
            raise ReleaseUnexpectedSourceError(source)
        if source not in tracked:
            raise ReleaseDirtyTreeError
        sources.append(source)
    for source in tracked:
        relative = source.relative_to(integration)
        if not _is_runtime_source(relative):
            raise ReleaseUnexpectedSourceError(source)
    return tuple(sources)


def _is_ignored_cache(source: Path, integration: Path) -> bool:
    relative = source.relative_to(integration)
    return "__pycache__" in relative.parts or source.suffix in IGNORED_CACHE_SUFFIXES


def _is_runtime_source(relative: Path) -> bool:
    if relative.suffix == ".py":
        return True
    if relative.parts in {("manifest.json",), ("services.yaml",)}:
        return True
    if (
        len(relative.parts) == TRANSLATION_PATH_PARTS
        and relative.parts[0] == "brand"
        and relative.name in BRAND_FILENAMES
    ):
        return True
    return (
        len(relative.parts) == TRANSLATION_PATH_PARTS
        and relative.parts[0] == "translations"
        and relative.suffix == ".json"
    )
