from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from scripts.release import ReleaseCheckError, build_release, check_release
from tests.test_release_tool import release_root

if TYPE_CHECKING:
    from pathlib import Path


def _replace(path: Path, original: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert original in text
    _ = path.write_text(text.replace(original, replacement), encoding="utf-8")


def test_check_release_rejects_project_version_from_wrong_toml_section(
    tmp_path: Path,
) -> None:
    # Given
    root = release_root(tmp_path)
    pyproject = root / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    _ = pyproject.write_text(
        '[tool.release_decoy]\nversion = "0.1.0"\n\n'
        + original.replace('version = "0.1.0"', 'version = "0.2.0"'),
        encoding="utf-8",
    )

    # When / Then
    with pytest.raises(ReleaseCheckError, match="version"):
        _ = check_release(root)


def test_check_release_rejects_duplicate_manifest_version(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    manifest = root / "custom_components" / "geofence_journal" / "manifest.json"
    _replace(
        manifest,
        '"version":"0.1.0"',
        '"version":"0.1.0","version":"0.2.0"',
    )

    # When / Then
    with pytest.raises(ReleaseCheckError, match="version"):
        _ = check_release(root)


def test_check_release_rejects_lockfile_home_assistant_drift(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    _replace(root / "uv.lock", ">=2026.7,<2026.8", ">=2026.6,<2026.8")

    # When / Then
    with pytest.raises(ReleaseCheckError, match="Home Assistant"):
        _ = check_release(root)


def test_build_release_rejects_output_inside_integration(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    output = root / "custom_components" / "geofence_journal" / "packages"

    # When / Then
    with pytest.raises(ReleaseCheckError, match="output directory"):
        _ = build_release(root, output)


@pytest.mark.parametrize(
    ("relative", "original", "replacement", "error_match"),
    [
        (
            "pyproject.toml",
            'name = "hass-geofence-journal"',
            'name = "other"',
            "project name",
        ),
        ("pyproject.toml", ">=3.14.2,<3.15", ">=3.14.1,<3.15", "pyproject Python"),
        (
            "pyproject.toml",
            "homeassistant>=2026.7,<2026.8",
            "homeassistant>=2026.6,<2026.8",
            "pyproject Home Assistant",
        ),
        ("uv.lock", ">=3.14.2, <3.15", ">=3.14.1, <3.15", "lockfile Python"),
        ("hacs.json", "2026.7.0", "2026.6.0", "HACS Home Assistant"),
        ("README.md", "Python 3.14.2", "Python 3.14.1", "README"),
        (
            ".github/workflows/ci.yml",
            'python-version: "3.14.2"',
            'python-version: "3.14.3"',
            "CI Python",
        ),
        (
            "custom_components/geofence_journal/manifest.json",
            "geofence_journal",
            "other_domain",
            "manifest domain",
        ),
        (
            "custom_components/geofence_journal/const.py",
            'VERSION: Final = "0.1.0"',
            'VERSION: Final = "0.2.0"',
            "version",
        ),
    ],
)
def test_check_release_rejects_declaration_drift(
    tmp_path: Path,
    relative: str,
    original: str,
    replacement: str,
    error_match: str,
) -> None:
    # Given
    root = release_root(tmp_path)
    _replace(root / relative, original, replacement)

    # When / Then
    with pytest.raises(ReleaseCheckError, match=error_match):
        _ = check_release(root)


def test_check_release_rejects_malformed_manifest_json(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    manifest = root / "custom_components" / "geofence_journal" / "manifest.json"
    _ = manifest.write_text("{", encoding="utf-8")

    # When / Then
    with pytest.raises(ReleaseCheckError, match="invalid release metadata"):
        _ = check_release(root)


def test_check_release_rejects_unsupported_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    root = release_root(tmp_path)
    monkeypatch.setattr(sys, "version_info", (3, 13, 9))

    # When / Then
    with pytest.raises(ReleaseCheckError, match=r"Python 3\.14\.2"):
        _ = check_release(root)


def test_build_release_rejects_source_symlink(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    integration = root / "custom_components" / "geofence_journal"
    link = integration / "manifest-link.json"
    try:
        link.symlink_to(integration / "manifest.json")
    except OSError as error:
        pytest.skip(str(error))

    # When / Then
    with pytest.raises(ReleaseCheckError, match="symlink"):
        _ = build_release(root, tmp_path / "dist")
