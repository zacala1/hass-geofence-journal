"""Fail-closed Git inspection for release tooling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING, Final

from scripts.release_errors import ReleaseDirtyTreeError, ReleaseRepositoryError

if TYPE_CHECKING:
    from collections.abc import Sequence

GIT_EXECUTABLE: Final = which("git")


def validate_clean_worktree(root: Path) -> None:
    """Reject tracked or untracked changes before packaging."""
    result = _run_git(
        root,
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
    )
    if result.stdout:
        raise ReleaseDirtyTreeError


def tracked_integration_files(root: Path, integration: Path) -> frozenset[Path]:
    """Return only Git-tracked files inside the integration package."""
    relative = integration.relative_to(root).as_posix()
    result = _run_git(root, ("ls-files", "-z", "--", relative))
    return frozenset(
        root.joinpath(*Path(entry).parts)
        for entry in result.stdout.split("\0")
        if entry
    )


def _run_git(root: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    if GIT_EXECUTABLE is None:
        raise ReleaseRepositoryError
    try:
        result = subprocess.run(  # noqa: S603  # Fixed Git binary, no shell.
            (GIT_EXECUTABLE, "-C", str(root), *arguments),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise ReleaseRepositoryError from error
    if result.returncode != 0:
        raise ReleaseRepositoryError
    return result
