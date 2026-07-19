# /// script
# requires-python = ">=3.14.2,<3.15"
# dependencies = []
# ///
# ─── How to run ───
# uv run python -m scripts.release check [vX.Y.Z]
# uv run python -m scripts.release build [output-directory]
"""Validate and build one Geofence Journal release artifact."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.release_archive import build_release
from scripts.release_contract import ReleaseContract, check_release
from scripts.release_errors import ReleaseCheckError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    "ReleaseCheckError",
    "ReleaseContract",
    "build_release",
    "check_release",
    "run_cli",
)


def run_cli(arguments: Sequence[str], root: Path) -> int:
    """Run the release tool against an explicit repository root."""
    match tuple(arguments):
        case ("check",):
            _write_ready(check_release(root))
            return 0
        case ("check", tag):
            _write_ready(check_release(root, tag))
            return 0
        case ("build",):
            _write_artifact(build_release(root, root / "dist"))
            return 0
        case ("build", output):
            output_path = Path(output)
            destination = (
                output_path if output_path.is_absolute() else root / output_path
            )
            _write_artifact(build_release(root, destination))
            return 0
        case _:
            _ = sys.stderr.write(
                "usage: release.py check [vX.Y.Z] | build [output-directory]\n"
            )
            return 2


def main() -> int:
    """Run the release tool from the current repository root."""
    try:
        return run_cli(sys.argv[1:], Path.cwd())
    except ReleaseCheckError as error:
        _ = sys.stderr.write(f"release-check failed: {error}\n")
        return 1


def _write_ready(contract: ReleaseContract) -> None:
    message = " ".join(
        (
            f"release-ready version={contract.version}",
            f"home_assistant>={contract.minimum_home_assistant}",
            f"python{contract.python_requirement}",
        )
    )
    _ = sys.stdout.write(message + "\n")


def _write_artifact(artifact: Path) -> None:
    _ = sys.stdout.write(f"release-artifact {artifact}\n")


if __name__ == "__main__":
    raise SystemExit(main())
