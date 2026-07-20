# /// script
# requires-python = ">=3.14.2,<3.15"
# dependencies = ["pydantic>=2.12.5,<3"]
# ///
# ─── How to run ───
# uv run python -m scripts.release check [v<PEP-440-version>]
# uv run python -m scripts.release build [output-directory]
"""Validate and build one Geofence Journal release artifact."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

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
USAGE: Final = (
    "usage: release.py check [v<version>] | classify v<version> "
    "| build [output-directory]\n"
)


def run_cli(arguments: Sequence[str], root: Path) -> int:
    """Run the release tool against an explicit repository root."""
    try:
        return _dispatch_cli(arguments, root)
    except ReleaseCheckError as error:
        _ = sys.stderr.write(f"release-check failed: {error}\n")
        return 1


def _dispatch_cli(arguments: Sequence[str], root: Path) -> int:
    match tuple(arguments):
        case ("--help",) | ("-h",):
            _ = sys.stdout.write(USAGE)
            exit_code = 0
        case ("check",):
            _write_ready(check_release(root))
            exit_code = 0
        case ("check", tag):
            _write_ready(check_release(root, tag))
            exit_code = 0
        case ("classify", tag):
            _write_classification(check_release(root, tag))
            exit_code = 0
        case ("build",):
            _write_artifact(build_release(root, root / "dist"))
            exit_code = 0
        case ("build", output):
            output_path = Path(output)
            destination = (
                output_path if output_path.is_absolute() else root / output_path
            )
            _write_artifact(build_release(root, destination))
            exit_code = 0
        case _:
            _ = sys.stderr.write(USAGE)
            exit_code = 2
    return exit_code


def main() -> int:
    """Run the release tool from the current repository root."""
    return run_cli(sys.argv[1:], Path.cwd())


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


def _write_classification(contract: ReleaseContract) -> None:
    value = str(contract.prerelease).lower()
    _ = sys.stdout.write(f"prerelease={value}\n")


if __name__ == "__main__":
    raise SystemExit(main())
