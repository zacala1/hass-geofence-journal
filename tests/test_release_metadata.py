from pathlib import Path
from typing import Final, TypedDict

from pydantic import TypeAdapter

ROOT: Final = Path(__file__).parents[1]
REPOSITORY_URL: Final = "https://github.com/zacala1/hass-geofence-journal"


class ReleaseManifest(TypedDict):
    domain: str
    name: str
    version: str
    documentation: str
    issue_tracker: str
    codeowners: list[str]
    config_flow: bool
    single_config_entry: bool
    integration_type: str
    iot_class: str
    dependencies: list[str]
    requirements: list[str]


class HacsMetadata(TypedDict):
    name: str
    content_in_root: bool
    homeassistant: str


def test_manifest_describes_the_custom_integration_release() -> None:
    # Given: the integration manifest at the HACS package boundary.
    manifest = TypeAdapter(ReleaseManifest).validate_json(
        (ROOT / "custom_components" / "geofence_journal" / "manifest.json").read_text(
            "utf-8"
        ),
        extra="forbid",
    )

    # When: release identity and behavior metadata are read.
    release_contract = (
        manifest["domain"],
        manifest["name"],
        manifest["version"],
        manifest["integration_type"],
        manifest["iot_class"],
    )

    # Then: metadata truthfully describes one calculated local journal service.
    assert release_contract == (
        "geofence_journal",
        "Geofence Journal",
        "0.1.0",
        "service",
        "calculated",
    )
    assert manifest["documentation"] == REPOSITORY_URL
    assert manifest["issue_tracker"] == f"{REPOSITORY_URL}/issues"
    assert manifest["codeowners"] == ["@zacala1"]
    assert manifest["config_flow"] is True
    assert manifest["single_config_entry"] is True
    assert manifest["dependencies"] == ["http"]
    assert manifest["requirements"] == []


def test_hacs_metadata_targets_the_supported_home_assistant_release() -> None:
    # Given: root-level HACS metadata for a nested custom component.
    metadata = TypeAdapter(HacsMetadata).validate_json(
        (ROOT / "hacs.json").read_text("utf-8"),
        extra="forbid",
    )

    # When: the install layout and compatibility floor are read.
    package_contract = (
        metadata["name"],
        metadata["content_in_root"],
        metadata["homeassistant"],
    )

    # Then: HACS installs the nested integration only on its tested HA baseline.
    assert package_contract == ("Geofence Journal", False, "2026.7.0")


def test_readme_documents_the_release_safety_contract() -> None:
    # Given: the user-facing release documentation.
    readme = " ".join((ROOT / "README.md").read_text("utf-8").split())

    # When: required operational promises are collected.
    required_phrases = {
        "Python 3.14.2",
        "single config entry",
        "coordinates are not stored by default",
        "indefinitely",
        "UTF-8 BOM",
        "24 hours",
        "dry-run",
        "DELETE ALL GEOFENCE JOURNAL DATA",
        "no automatic backup",
        "never silently",
        "Recorder",
        ".storage/geofence_journal/geofence_journal.db",
    }

    # Then: installation, privacy, retention, and destructive recovery are explicit.
    assert required_phrases <= {
        phrase for phrase in required_phrases if phrase in readme
    }
    assert "HACS" in readme
    assert "Manual installation" in readme
    assert "Home Assistant 2026.7" in readme
    assert "frontend" in readme


def test_ci_workflow_runs_every_local_release_gate() -> None:
    # Given: the repository quality workflow.
    workflow = " ".join(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8").split()
    )

    # When: its immutable release commands are inspected.
    commands = {
        "uv sync --all-groups --frozen",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run basedpyright",
        "uv run pytest --cov=custom_components.geofence_journal --cov-fail-under=95",
    }

    # Then: CI enforces sync, lint, format, types, tests, and the coverage floor.
    assert commands <= {command for command in commands if command in workflow}
    assert 'python-version: "3.14.2"' in workflow


def test_validation_workflow_uses_official_hacs_and_hassfest_actions() -> None:
    # Given: the repository validation workflow.
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text("utf-8")

    # When: the external validators are inspected.
    expected_actions = {
        "hacs/action@d556e736723344f83838d08488c983a15381059a",
        "home-assistant/actions/hassfest@f4ca6f671bd429efb108c0f2fa0ae8af0215986c",
    }

    # Then: both official custom-integration validators are scheduled.
    assert expected_actions <= {
        action for action in expected_actions if action in workflow
    }
    assert 'category: "integration"' in workflow
    assert "schedule:" in workflow
