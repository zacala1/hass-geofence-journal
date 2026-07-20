"""Validate the repository contract required for a release."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from scripts.release_errors import (
    MissingReleaseFilesError,
    ReleaseEnvironmentError,
    ReleaseMismatchError,
    RepositoryRootError,
)
from scripts.release_metadata import ReleaseMetadata, load_release_metadata

if TYPE_CHECKING:
    from pathlib import Path

PROJECT_NAME: Final = "hass-geofence-journal"
DOMAIN: Final = "geofence_journal"
PYTHON_REQUIREMENT: Final = ">=3.14.2,<3.15"
LOCK_PYTHON_REQUIREMENT: Final = ">=3.14.2, <3.15"
HOME_ASSISTANT_REQUIREMENT: Final = "homeassistant>=2026.7,<2026.8"
LOCK_HOME_ASSISTANT_REQUIREMENT: Final = ">=2026.7,<2026.8"
MINIMUM_HOME_ASSISTANT: Final = "2026.7.0"
MINIMUM_PYTHON: Final = (3, 14, 2)
MAXIMUM_PYTHON: Final = (3, 15)
VERSION_PATTERN: Final = re.compile(
    r"(?P<release>\d+\.\d+\.\d+)(?:(?P<phase>a|b|rc)(?P<number>[1-9]\d*))?"
)
RELEASE_FILENAME: Final = "geofence_journal.zip"
REQUIRED_INTEGRATION_FILES: Final = (
    "__init__.py",
    "backup.py",
    "binary_sensor.py",
    "config_flow.py",
    "const.py",
    "manifest.json",
    "sensor.py",
    "services.yaml",
    "translations/en.json",
    "translations/ko.json",
)
PROJECT_NAME_FIELD: Final = "project name"
VERSION_FIELD: Final = "version"
SEMANTIC_VERSION_FIELD: Final = "semantic version"
MANIFEST_DOMAIN_FIELD: Final = "manifest domain"
PROJECT_PYTHON_FIELD: Final = "pyproject Python requirement"
LOCK_PYTHON_FIELD: Final = "lockfile Python requirement"
PROJECT_HOME_ASSISTANT_FIELD: Final = "pyproject Home Assistant requirement"
LOCK_HOME_ASSISTANT_FIELD: Final = "lockfile Home Assistant requirement"
HACS_VERSION_FIELD: Final = "HACS Home Assistant version"
HACS_FILENAME_FIELD: Final = "HACS release filename"
HACS_ZIP_RELEASE_FIELD: Final = "HACS zip release setting"
HACS_DEFAULT_BRANCH_FIELD: Final = "HACS default branch visibility"
RELEASE_TAG_FIELD: Final = "release tag"


@dataclass(frozen=True, slots=True)
class ReleaseContract:
    """Validated release identity and supported environment."""

    root: Path
    version: str
    domain: str
    minimum_home_assistant: str
    python_requirement: str
    prerelease: bool


def check_release(root: Path, expected_tag: str | None = None) -> ReleaseContract:
    """Validate repository root, version identity, and runtime requirements."""
    resolved = _validate_root(root)
    _validate_interpreter()
    metadata = load_release_metadata(
        resolved,
        project_name=PROJECT_NAME,
        domain=DOMAIN,
    )
    version, prerelease = _validate_identity(metadata)
    _validate_environment(metadata, version)
    _validate_tag(version, expected_tag)
    _validate_integration_files(metadata.manifest_path.parent)
    return ReleaseContract(
        root=resolved,
        version=version,
        domain=metadata.manifest_domain,
        minimum_home_assistant=metadata.hacs_home_assistant_version,
        python_requirement=PYTHON_REQUIREMENT,
        prerelease=prerelease,
    )


def _validate_root(root: Path) -> Path:
    resolved = root.resolve()
    if not (resolved / ".git").exists() or not (resolved / "pyproject.toml").is_file():
        raise RepositoryRootError(resolved)
    return resolved


def _validate_interpreter() -> None:
    if sys.version_info < MINIMUM_PYTHON or sys.version_info >= MAXIMUM_PYTHON:
        raise ReleaseEnvironmentError


def _validate_identity(metadata: ReleaseMetadata) -> tuple[str, bool]:
    if metadata.project_name != PROJECT_NAME:
        raise ReleaseMismatchError(
            PROJECT_NAME_FIELD,
            PROJECT_NAME,
            metadata.project_name,
        )
    version = metadata.project_version
    version_match = VERSION_PATTERN.fullmatch(version)
    if version_match is None:
        raise ReleaseMismatchError(
            SEMANTIC_VERSION_FIELD,
            "X.Y.Z, X.Y.ZbN, or X.Y.ZrcN",
            version,
        )
    declared = (
        metadata.manifest_version,
        metadata.constants_version,
        metadata.lock_version,
    )
    if any(candidate != version for candidate in declared):
        raise ReleaseMismatchError(VERSION_FIELD, version, ", ".join(declared))
    if (
        metadata.manifest_domain != DOMAIN
        or metadata.manifest_path.parent.name != metadata.manifest_domain
    ):
        raise ReleaseMismatchError(
            MANIFEST_DOMAIN_FIELD,
            DOMAIN,
            metadata.manifest_domain,
        )
    return version, version_match.group("phase") is not None


def _validate_environment(metadata: ReleaseMetadata, version: str) -> None:
    _require_equal(
        PROJECT_PYTHON_FIELD,
        PYTHON_REQUIREMENT,
        metadata.project_python_requirement,
    )
    _require_equal(
        LOCK_PYTHON_FIELD,
        LOCK_PYTHON_REQUIREMENT,
        metadata.lock_python_requirement,
    )
    if HOME_ASSISTANT_REQUIREMENT not in metadata.project_dependencies:
        raise ReleaseMismatchError(
            PROJECT_HOME_ASSISTANT_FIELD,
            HOME_ASSISTANT_REQUIREMENT,
            "not declared",
        )
    _require_equal(
        LOCK_HOME_ASSISTANT_FIELD,
        LOCK_HOME_ASSISTANT_REQUIREMENT,
        metadata.lock_home_assistant_requirement,
    )
    _require_equal(
        HACS_VERSION_FIELD,
        MINIMUM_HOME_ASSISTANT,
        metadata.hacs_home_assistant_version,
    )
    _require_equal(HACS_FILENAME_FIELD, RELEASE_FILENAME, metadata.hacs_filename)
    _require_equal(
        HACS_ZIP_RELEASE_FIELD,
        "true",
        str(metadata.hacs_zip_release).lower(),
    )
    _require_equal(
        HACS_DEFAULT_BRANCH_FIELD,
        "true",
        str(metadata.hacs_hide_default_branch).lower(),
    )
    for marker in (f"Version {version}", "Python 3.14.2", "Home Assistant 2026.7"):
        _require_marker(metadata.readme, marker, "README environment/version")
    _require_marker(
        metadata.workflow,
        'python-version: "3.14.2"',
        "CI Python version",
    )


def _validate_tag(version: str, expected_tag: str | None) -> None:
    if expected_tag is not None and expected_tag != f"v{version}":
        raise ReleaseMismatchError(RELEASE_TAG_FIELD, f"v{version}", expected_tag)


def _validate_integration_files(integration: Path) -> None:
    missing = tuple(
        relative
        for relative in REQUIRED_INTEGRATION_FILES
        if not (integration / relative).is_file()
    )
    if missing:
        raise MissingReleaseFilesError(missing)


def _require_equal(field: str, expected: str, actual: str) -> None:
    if actual != expected:
        raise ReleaseMismatchError(field, expected, actual)


def _require_marker(text: str, marker: str, field: str) -> None:
    if marker not in text:
        raise ReleaseMismatchError(field, marker, "not declared")
