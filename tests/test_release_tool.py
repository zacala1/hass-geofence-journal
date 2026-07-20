from __future__ import annotations

import json
from typing import TYPE_CHECKING
from zipfile import ZipFile

import pytest
from scripts.release import (
    ReleaseCheckError,
    build_release,
    check_release,
    run_cli,
)

if TYPE_CHECKING:
    from pathlib import Path


def release_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    integration = root / "custom_components" / "geofence_journal"
    translations = integration / "translations"
    translations.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    _ = (root / "pyproject.toml").write_text(
        """[project]
name = "hass-geofence-journal"
version = "0.1.0"
requires-python = ">=3.14.2,<3.15"
dependencies = ["homeassistant>=2026.7,<2026.8"]
""",
        encoding="utf-8",
    )
    _ = (root / "uv.lock").write_text(
        """version = 1
requires-python = ">=3.14.2, <3.15"

[[package]]
name = "hass-geofence-journal"
version = "0.1.0"

[package.metadata]
requires-dist = [
    { name = "homeassistant", specifier = ">=2026.7,<2026.8" },
]
""",
        encoding="utf-8",
    )
    _ = (root / "hacs.json").write_text(
        json.dumps(
            {
                "name": "Geofence Journal",
                "homeassistant": "2026.7.0",
                "zip_release": True,
                "filename": "geofence_journal.zip",
                "hide_default_branch": True,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    _ = (root / "README.md").write_text(
        "Geofence Journal Version 0.1.0\nPython 3.14.2\nHome Assistant 2026.7\n",
        encoding="utf-8",
    )
    _ = (root / ".github" / "workflows" / "ci.yml").write_text(
        'python-version: "3.14.2"\n', encoding="utf-8"
    )
    _ = (integration / "manifest.json").write_text(
        '{"domain":"geofence_journal","version":"0.1.0"}',
        encoding="utf-8",
    )
    _ = (integration / "const.py").write_text(
        'VERSION: Final = "0.1.0"\n', encoding="utf-8"
    )
    for relative in (
        "__init__.py",
        "backup.py",
        "config_flow.py",
        "services.yaml",
        "sensor.py",
        "binary_sensor.py",
        "translations/en.json",
        "translations/ko.json",
    ):
        _ = (integration / relative).write_text("{}\n", encoding="utf-8")
    return root


def test_check_release_accepts_consistent_root(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)

    # When
    contract = check_release(root, "v0.1.0")

    # Then
    assert contract.version == "0.1.0"
    assert contract.domain == "geofence_journal"
    assert contract.minimum_home_assistant == "2026.7.0"
    assert contract.python_requirement == ">=3.14.2,<3.15"
    assert contract.prerelease is False


def test_check_release_accepts_pep440_beta_and_exact_tag(tmp_path: Path) -> None:
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
            path.read_text(encoding="utf-8").replace("0.1.0", "0.1.0b1"),
            encoding="utf-8",
        )

    contract = check_release(root, "v0.1.0b1")

    assert contract.version == "0.1.0b1"
    assert contract.prerelease is True


def test_check_release_rejects_manifest_version_drift(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    manifest = root / "custom_components" / "geofence_journal" / "manifest.json"
    _ = manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("0.1.0", "0.2.0"),
        encoding="utf-8",
    )

    # When / Then
    with pytest.raises(ReleaseCheckError, match="version"):
        _ = check_release(root)


def test_check_release_rejects_wrong_tag(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)

    # When / Then
    with pytest.raises(ReleaseCheckError, match="tag"):
        _ = check_release(root, "v0.2.0")


def test_check_release_rejects_non_root_directory(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    nested = root / "custom_components"

    # When / Then
    with pytest.raises(ReleaseCheckError, match="repository root"):
        _ = check_release(nested)


def test_check_release_rejects_incomplete_integration(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    (root / "custom_components" / "geofence_journal" / "services.yaml").unlink()

    # When / Then
    with pytest.raises(ReleaseCheckError, match=r"services\.yaml"):
        _ = check_release(root)


def test_build_release_creates_reproducible_install_tree(tmp_path: Path) -> None:
    # Given
    root = release_root(tmp_path)
    cache = root / "custom_components" / "geofence_journal" / "__pycache__"
    cache.mkdir()
    _ = (cache / "runtime.pyc").write_bytes(b"cache")

    # When
    first = build_release(root, tmp_path / "first")
    second = build_release(root, tmp_path / "second")

    # Then
    assert first.name == "geofence_journal.zip"
    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "backup.py" in names
    assert "translations/ko.json" in names
    assert all(not name.startswith("custom_components/") for name in names)
    assert all("__pycache__" not in name for name in names)


def test_cli_check_reports_deployment_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = release_root(tmp_path)

    # When
    exit_code = run_cli(("check", "v0.1.0"), root)

    # Then
    assert exit_code == 0
    assert capsys.readouterr().out == (
        "release-ready version=0.1.0 home_assistant>=2026.7.0 python>=3.14.2,<3.15\n"
    )


def test_cli_build_uses_requested_output_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = release_root(tmp_path)

    # When
    exit_code = run_cli(("build", "packages"), root)

    # Then
    artifact = root / "packages" / "geofence_journal.zip"
    assert exit_code == 0
    assert artifact.is_file()
    assert capsys.readouterr().out == f"release-artifact {artifact.resolve()}\n"


def test_cli_classify_outputs_github_prerelease_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
            path.read_text(encoding="utf-8").replace("0.1.0", "0.1.0b1"),
            encoding="utf-8",
        )

    exit_code = run_cli(("classify", "v0.1.0b1"), root)

    assert exit_code == 0
    assert capsys.readouterr().out == "prerelease=true\n"


def test_cli_rejects_unknown_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = release_root(tmp_path)

    # When
    exit_code = run_cli(("publish",), root)

    # Then
    assert exit_code == 2
    assert capsys.readouterr().err.startswith("usage: release.py")


def test_cli_help_reports_usage_successfully(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = release_root(tmp_path)

    # When
    exit_code = run_cli(("--help",), root)

    # Then
    assert exit_code == 0
    assert capsys.readouterr().out.startswith("usage: release.py")


def test_cli_build_reports_unusable_output_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = release_root(tmp_path)
    output = root / "not-a-directory"
    _ = output.write_text("occupied", encoding="utf-8")

    # When
    exit_code = run_cli(("build", str(output)), root)

    # Then
    assert exit_code == 1
    assert "release-check failed" in capsys.readouterr().err
