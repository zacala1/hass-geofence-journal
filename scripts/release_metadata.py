"""Parse release metadata into strict structured models."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scripts.release_errors import (
    InvalidReleaseMetadataError,
    MissingReleaseFieldError,
    ReleaseFileReadError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CONST_VERSION_PATTERN = re.compile(
    r'^VERSION: Final = "(?P<value>\d+\.\d+\.\d+(?:(?:a|b|rc)[1-9]\d*)?)"$',
    re.MULTILINE,
)
LOCKFILE_PROJECT_PACKAGE_FIELD: Final = "lockfile project package"
LOCKFILE_VERSION_FIELD: Final = "lockfile version"
LOCKFILE_PROJECT_METADATA_FIELD: Final = "lockfile project metadata"
CONSTANT_VERSION_FIELD: Final = "constant version"


class _JsonLoader(Protocol):
    """Typed boundary for the untyped standard-library JSON result."""

    def __call__(
        self,
        source: str,
        /,
        *,
        object_pairs_hook: Callable[
            [list[tuple[str, object]]],
            dict[str, object],
        ],
    ) -> object: ...


class _ReleaseModel(BaseModel):
    """Base model for strict release-file parsing."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)


class _ProjectSection(_ReleaseModel):
    """Relevant fields from the pyproject project table."""

    name: str
    version: str
    requires_python: str = Field(alias="requires-python")
    dependencies: list[str]


class _ProjectFile(_ReleaseModel):
    """Relevant pyproject document shape."""

    project: _ProjectSection


class _LockedRequirement(_ReleaseModel):
    """One declared project requirement in the lockfile."""

    name: str
    specifier: str | None = None


class _LockedPackageMetadata(_ReleaseModel):
    """Project-package requirement metadata in the lockfile."""

    requires_dist: list[_LockedRequirement] = Field(
        default_factory=list,
        alias="requires-dist",
    )


class _LockedPackage(_ReleaseModel):
    """One package entry in the lockfile."""

    name: str
    version: str | None = None
    metadata: _LockedPackageMetadata | None = None


class _LockFile(_ReleaseModel):
    """Relevant uv lockfile document shape."""

    requires_python: str = Field(alias="requires-python")
    packages: list[_LockedPackage] = Field(alias="package")


class _ManifestFile(_ReleaseModel):
    """Relevant Home Assistant manifest fields."""

    domain: str
    version: str


class _HacsFile(_ReleaseModel):
    """Relevant HACS metadata fields."""

    filename: str
    hide_default_branch: bool
    homeassistant: str
    zip_release: bool


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    """Structured release declarations read from repository files."""

    manifest_path: Path
    project_name: str
    project_version: str
    project_python_requirement: str
    project_dependencies: tuple[str, ...]
    lock_version: str
    lock_python_requirement: str
    lock_home_assistant_requirement: str
    manifest_version: str
    manifest_domain: str
    constants_version: str
    hacs_filename: str
    hacs_hide_default_branch: bool
    hacs_home_assistant_version: str
    hacs_zip_release: bool
    readme: str
    workflow: str


def load_release_metadata(
    root: Path,
    *,
    project_name: str,
    domain: str,
) -> ReleaseMetadata:
    """Read and strictly parse all release identity declarations."""
    manifest_path = root / "custom_components" / domain / "manifest.json"
    pyproject = _parse_toml(_ProjectFile, root / "pyproject.toml")
    lockfile = _parse_toml(_LockFile, root / "uv.lock")
    manifest = _parse_json(_ManifestFile, manifest_path)
    hacs = _parse_json(_HacsFile, root / "hacs.json")
    locked_project = _locked_project(lockfile, project_name)
    return ReleaseMetadata(
        manifest_path=manifest_path,
        project_name=pyproject.project.name,
        project_version=pyproject.project.version,
        project_python_requirement=pyproject.project.requires_python,
        project_dependencies=tuple(pyproject.project.dependencies),
        lock_version=_required_lock_version(locked_project),
        lock_python_requirement=lockfile.requires_python,
        lock_home_assistant_requirement=_locked_requirement(
            locked_project,
            "homeassistant",
        ),
        manifest_version=manifest.version,
        manifest_domain=manifest.domain,
        constants_version=_constant_version(manifest_path.with_name("const.py")),
        hacs_filename=hacs.filename,
        hacs_hide_default_branch=hacs.hide_default_branch,
        hacs_home_assistant_version=hacs.homeassistant,
        hacs_zip_release=hacs.zip_release,
        readme=_read_text(root / "README.md"),
        workflow=_read_text(root / ".github" / "workflows" / "ci.yml"),
    )


def _parse_toml[T: BaseModel](model: type[T], path: Path) -> T:
    try:
        document: object = tomllib.loads(_read_text(path))
        return model.model_validate(document)
    except (tomllib.TOMLDecodeError, ValidationError) as error:
        raise InvalidReleaseMetadataError(path) from error


def _parse_json[T: BaseModel](model: type[T], path: Path) -> T:
    try:
        document = _decode_json(json.loads, _read_text(path))
        return model.model_validate(document)
    except ValueError as error:
        raise InvalidReleaseMetadataError(path) from error


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(key)
        document[key] = value
    return document


def _decode_json(loader: _JsonLoader, source: str) -> object:
    return loader(source, object_pairs_hook=_reject_duplicate_json_keys)


def _locked_project(lockfile: _LockFile, project_name: str) -> _LockedPackage:
    package = next(
        (
            candidate
            for candidate in lockfile.packages
            if candidate.name == project_name
        ),
        None,
    )
    if package is None:
        raise MissingReleaseFieldError(LOCKFILE_PROJECT_PACKAGE_FIELD)
    return package


def _required_lock_version(package: _LockedPackage) -> str:
    if package.version is None:
        raise MissingReleaseFieldError(LOCKFILE_VERSION_FIELD)
    return package.version


def _locked_requirement(package: _LockedPackage, name: str) -> str:
    metadata = package.metadata
    if metadata is None:
        raise MissingReleaseFieldError(LOCKFILE_PROJECT_METADATA_FIELD)
    requirement = next(
        (candidate for candidate in metadata.requires_dist if candidate.name == name),
        None,
    )
    if requirement is None or requirement.specifier is None:
        field = f"lockfile {name} requirement"
        raise MissingReleaseFieldError(field)
    return requirement.specifier


def _constant_version(path: Path) -> str:
    match = CONST_VERSION_PATTERN.search(_read_text(path))
    if match is None:
        raise MissingReleaseFieldError(CONSTANT_VERSION_FIELD)
    return match.group("value")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseFileReadError(path) from error
