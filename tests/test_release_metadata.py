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


def test_release_tooling_is_included_in_strict_project_gates() -> None:
    # Given: the project-level static analysis configuration.
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")

    # When: Ruff source roots and BasedPyright inputs are inspected.
    strict_inputs = {
        'src = ["custom_components", "scripts", "tests"]',
        'include = ["custom_components/geofence_journal", "scripts", "tests"]',
    }

    # Then: deployment tooling cannot bypass lint or strict typing.
    assert strict_inputs <= {
        declaration for declaration in strict_inputs if declaration in pyproject
    }


def test_release_workflow_verifies_the_tag_before_publishing() -> None:
    # Given: the tag-triggered release workflow.
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")

    # When: immutable actions and deployment commands are inspected.
    pinned_actions = {
        "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    }
    required_commands = {
        'uv run python -m scripts.release check "${GITHUB_REF_NAME}"',
        "uv run python -m scripts.release build dist",
        'gh release create "${GITHUB_REF_NAME}" dist/*.zip',
    }

    # Then: a verified artifact is the only input to a tag-only publication job.
    assert pinned_actions <= {action for action in pinned_actions if action in workflow}
    assert required_commands <= {
        command for command in required_commands if command in workflow
    }
    assert 'tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "needs:\n      - verify" in workflow
    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in workflow
    assert "contents: write" in workflow
    assert "GH_REPO: ${{ github.repository }}" in workflow
    assert workflow.index("-m scripts.release check") < workflow.index(
        "-m scripts.release build"
    )
    assert workflow.index("-m scripts.release build") < workflow.index(
        "gh release create"
    )


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
