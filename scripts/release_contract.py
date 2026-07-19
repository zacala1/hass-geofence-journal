"""Validate the repository contract required for a release."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from scripts.release_errors import (
    MissingReleaseFieldError,
    MissingReleaseFilesError,
    ReleaseEnvironmentError,
    ReleaseFileReadError,
    ReleaseMismatchError,
    RepositoryRootError,
)

if TYPE_CHECKING:
    from pathlib import Path

PROJECT_NAME: Final = "hass-geofence-journal"
DOMAIN: Final = "geofence_journal"
PYTHON_REQUIREMENT: Final = ">=3.14.2,<3.15"
LOCK_PYTHON_REQUIREMENT: Final = ">=3.14.2, <3.15"
HOME_ASSISTANT_REQUIREMENT: Final = "homeassistant>=2026.7,<2026.8"
MINIMUM_HOME_ASSISTANT: Final = "2026.7.0"
MINIMUM_PYTHON: Final = (3, 14, 2)
MAXIMUM_PYTHON: Final = (3, 15)
PROJECT_VERSION_PATTERN: Final = re.compile(
    r'^version = "(?P<value>\d+\.\d+\.\d+)"$', re.MULTILINE
)
LOCK_VERSION_PATTERN: Final = re.compile(
    "".join(
        (
            rf'(?ms)^\[\[package\]\]\nname = "{PROJECT_NAME}"\n',
            r'version = "(?P<value>\d+\.\d+\.\d+)"',
        )
    )
)
MANIFEST_VERSION_PATTERN: Final = re.compile(
    r'"version"\s*:\s*"(?P<value>\d+\.\d+\.\d+)"'
)
MANIFEST_DOMAIN_PATTERN: Final = re.compile(
    r'"domain"\s*:\s*"(?P<value>[a-z][a-z0-9_]+)"'
)
CONST_VERSION_PATTERN: Final = re.compile(
    r'^VERSION: Final = "(?P<value>\d+\.\d+\.\d+)"$', re.MULTILINE
)
HACS_HOME_ASSISTANT_PATTERN: Final = re.compile(
    r'"homeassistant"\s*:\s*"(?P<value>\d+\.\d+\.\d+)"'
)
REQUIRED_INTEGRATION_FILES: Final = (
    "__init__.py",
    "binary_sensor.py",
    "config_flow.py",
    "const.py",
    "manifest.json",
    "sensor.py",
    "services.yaml",
    "translations/en.json",
    "translations/ko.json",
)
VERSION_FIELD: Final = "version"
MANIFEST_DOMAIN_FIELD: Final = "manifest domain"
HACS_VERSION_FIELD: Final = "HACS Home Assistant version"
RELEASE_TAG_FIELD: Final = "release tag"


@dataclass(frozen=True, slots=True)
class ReleaseContract:
    """Validated release identity and supported environment."""

    root: Path
    version: str
    domain: str
    minimum_home_assistant: str
    python_requirement: str


@dataclass(frozen=True, slots=True)
class _ReleaseFiles:
    """Text inputs used to validate a release."""

    manifest_path: Path
    pyproject: str
    lockfile: str
    manifest: str
    constants: str
    hacs: str
    readme: str
    workflow: str


def check_release(root: Path, expected_tag: str | None = None) -> ReleaseContract:
    """Validate repository root, version identity, and runtime requirements."""
    resolved = _validate_root(root)
    _validate_interpreter()
    files = _load_release_files(resolved)
    version = _validate_versions(files)
    domain = _validate_domain(files)
    minimum_ha = _validate_environment(files, version)
    _validate_tag(version, expected_tag)
    _validate_integration_files(files.manifest_path.parent)
    return ReleaseContract(
        root=resolved,
        version=version,
        domain=domain,
        minimum_home_assistant=minimum_ha,
        python_requirement=PYTHON_REQUIREMENT,
    )


def _validate_root(root: Path) -> Path:
    resolved = root.resolve()
    if not (resolved / ".git").exists() or not (resolved / "pyproject.toml").is_file():
        raise RepositoryRootError(resolved)
    return resolved


def _validate_interpreter() -> None:
    if sys.version_info < MINIMUM_PYTHON or sys.version_info >= MAXIMUM_PYTHON:
        raise ReleaseEnvironmentError


def _load_release_files(root: Path) -> _ReleaseFiles:
    manifest_path = root / "custom_components" / DOMAIN / "manifest.json"
    return _ReleaseFiles(
        manifest_path=manifest_path,
        pyproject=_read_text(root / "pyproject.toml"),
        lockfile=_read_text(root / "uv.lock"),
        manifest=_read_text(manifest_path),
        constants=_read_text(manifest_path.with_name("const.py")),
        hacs=_read_text(root / "hacs.json"),
        readme=_read_text(root / "README.md"),
        workflow=_read_text(root / ".github" / "workflows" / "ci.yml"),
    )


def _validate_versions(files: _ReleaseFiles) -> str:
    version = _extract(PROJECT_VERSION_PATTERN, files.pyproject, "project version")
    declared = (
        _extract(MANIFEST_VERSION_PATTERN, files.manifest, "manifest version"),
        _extract(CONST_VERSION_PATTERN, files.constants, "constant version"),
        _extract(LOCK_VERSION_PATTERN, files.lockfile, "lockfile version"),
    )
    if any(candidate != version for candidate in declared):
        raise ReleaseMismatchError(VERSION_FIELD, version, ", ".join(declared))
    return version


def _validate_domain(files: _ReleaseFiles) -> str:
    domain = _extract(MANIFEST_DOMAIN_PATTERN, files.manifest, "manifest domain")
    if domain != DOMAIN or files.manifest_path.parent.name != domain:
        raise ReleaseMismatchError(MANIFEST_DOMAIN_FIELD, DOMAIN, domain)
    return domain


def _validate_environment(files: _ReleaseFiles, version: str) -> str:
    _require_marker(
        files.pyproject,
        f'requires-python = "{PYTHON_REQUIREMENT}"',
        "pyproject Python requirement",
    )
    _require_marker(
        files.pyproject,
        f'"{HOME_ASSISTANT_REQUIREMENT}"',
        "pyproject Home Assistant requirement",
    )
    _require_marker(
        files.lockfile,
        f'requires-python = "{LOCK_PYTHON_REQUIREMENT}"',
        "lockfile Python requirement",
    )
    minimum_ha = _extract(
        HACS_HOME_ASSISTANT_PATTERN,
        files.hacs,
        "minimum Home Assistant version",
    )
    if minimum_ha != MINIMUM_HOME_ASSISTANT:
        raise ReleaseMismatchError(
            HACS_VERSION_FIELD, MINIMUM_HOME_ASSISTANT, minimum_ha
        )
    for marker in (
        f"Version {version}",
        "Python 3.14.2",
        "Home Assistant 2026.7",
    ):
        _require_marker(files.readme, marker, "README environment/version")
    _require_marker(files.workflow, 'python-version: "3.14.2"', "CI Python version")
    return minimum_ha


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


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseFileReadError(path) from error


def _extract(pattern: re.Pattern[str], text: str, field: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise MissingReleaseFieldError(field)
    return match.group("value")


def _require_marker(text: str, marker: str, field: str) -> None:
    if marker not in text:
        raise ReleaseMismatchError(field, marker, "not declared")
