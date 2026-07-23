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


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (
            '"version":"0.1.0"',
            '"version":"0.2.0","version":"0.1.0"',
        ),
        (
            '"version":"0.1.0"',
            '"version":"0.1.0","version":"0.1.0"',
        ),
        (
            '"domain":"geofence_journal"',
            '"domain":"other_domain","domain":"geofence_journal"',
        ),
    ],
)
def test_check_release_rejects_duplicate_manifest_key(
    tmp_path: Path,
    original: str,
    replacement: str,
) -> None:
    # Given
    root = release_root(tmp_path)
    manifest = root / "custom_components" / "geofence_journal" / "manifest.json"
    _replace(
        manifest,
        original,
        replacement,
    )

    # When / Then
    with pytest.raises(ReleaseCheckError, match="invalid release metadata"):
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


def test_check_release_rejects_dirty_tracked_file(tmp_path: Path) -> None:
    root = release_root(tmp_path)
    readme = root / "README.md"
    _ = readme.write_text(
        readme.read_text(encoding="utf-8") + "local edit\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseCheckError, match="clean working tree"):
        _ = check_release(root)


def test_check_release_rejects_untracked_runtime_source(tmp_path: Path) -> None:
    root = release_root(tmp_path)
    source = root / "custom_components" / "geofence_journal" / "untracked.py"
    _ = source.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ReleaseCheckError, match="clean working tree"):
        _ = check_release(root)


@pytest.mark.parametrize("filename", [".env", "journal.db"])
def test_build_release_rejects_unexpected_runtime_file(
    tmp_path: Path, filename: str
) -> None:
    root = release_root(tmp_path)
    source = root / "custom_components" / "geofence_journal" / filename
    _ = source.write_text("must not ship\n", encoding="utf-8")
    output = tmp_path / f"dist-{filename.removeprefix('.')}"

    with pytest.raises(ReleaseCheckError, match="unexpected release source"):
        _ = build_release(root, output)

    assert not output.exists()


def test_build_release_rejects_an_unknown_brand_asset(tmp_path: Path) -> None:
    root = release_root(tmp_path)
    source = (
        root / "custom_components" / "geofence_journal" / "brand" / "unexpected.png"
    )
    _ = source.write_bytes(b"\x89PNG\r\n\x1a\nunexpected")

    with pytest.raises(ReleaseCheckError, match="unexpected release source"):
        _ = build_release(root, tmp_path / "dist-brand")


def test_check_release_rejects_alpha_version(tmp_path: Path) -> None:
    root = release_root(tmp_path)
    for relative in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "custom_components/geofence_journal/manifest.json",
        "custom_components/geofence_journal/const.py",
    ):
        path = root / relative
        _ = path.write_text(
            path.read_text(encoding="utf-8").replace("0.1.0", "0.1.0a1"),
            encoding="utf-8",
        )

    with pytest.raises(ReleaseCheckError):
        _ = check_release(root, "v0.1.0a1")
