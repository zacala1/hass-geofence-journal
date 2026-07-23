from pathlib import Path
from typing import Final, TypedDict

from PIL import Image
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
    filename: str
    hide_default_branch: bool
    homeassistant: str
    zip_release: bool


def _manifest() -> ReleaseManifest:
    return TypeAdapter(ReleaseManifest).validate_json(
        (ROOT / "custom_components" / "geofence_journal" / "manifest.json").read_text(
            "utf-8"
        ),
        extra="forbid",
    )


def test_manifest_describes_the_custom_integration_release() -> None:
    # Given: the integration manifest at the HACS package boundary.
    manifest = _manifest()

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
        "0.1.0b2",
        "service",
        "calculated",
    )
    assert manifest["documentation"] == REPOSITORY_URL
    assert manifest["issue_tracker"] == f"{REPOSITORY_URL}/issues"
    assert manifest["codeowners"] == ["@zacala1"]
    assert manifest["config_flow"] is True
    assert manifest["single_config_entry"] is True
    assert manifest["dependencies"] == ["http"]


def test_manifest_does_not_repin_home_assistant_core_dependencies() -> None:
    # Given: Home Assistant 2026.7 already supplies the runtime libraries used here.
    manifest = _manifest()

    # When: Home Assistant resolves custom integration requirements.
    requirements = manifest["requirements"]

    # Then: the integration cannot conflict with HA patch-level dependency pins.
    assert requirements == []


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
        metadata["filename"],
        metadata["hide_default_branch"],
        metadata["homeassistant"],
        metadata["zip_release"],
    )

    # Then: HACS installs the nested integration only on its tested HA baseline.
    assert package_contract == (
        "Geofence Journal",
        False,
        "geofence_journal.zip",
        True,
        "2026.7.0",
        True,
    )


def test_local_brand_icons_match_home_assistant_image_contract() -> None:
    brand = ROOT / "custom_components" / "geofence_journal" / "brand"

    for filename, size in (("icon.png", 256), ("icon@2x.png", 512)):
        with Image.open(brand / filename) as icon:
            assert icon.format == "PNG"
            assert icon.mode == "RGBA"
            assert icon.size == (size, size)
            assert icon.getchannel("A").getpixel((0, 0)) == 0


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
        "v0.1.0b2",
        "HACS prerelease",
        "outside the Home Assistant configuration directory",
        "geofence_journal.list_resources",
        "geofence_journal.purge_retention",
        "binary_sensor.geofence_journal_healthy",
        "retention_days",
        "private diagnostics",
    }

    # Then: installation, privacy, retention, and destructive recovery are explicit.
    assert required_phrases <= {
        phrase for phrase in required_phrases if phrase in readme
    }
    assert "HACS" in readme
    assert "Manual installation" in readme
    assert "Home Assistant 2026.7" in readme
    assert "frontend" in readme


def test_changelog_identifies_the_current_preview_scope() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")

    assert "0.1.0b2" in changelog
    assert "resource discovery" in changelog
    assert "retention" in changelog
    assert "diagnostics" in changelog
    assert "brand" in changelog


def test_ci_workflow_runs_every_local_release_gate() -> None:
    # Given: the repository quality workflow.
    workflow = " ".join(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8").split()
    )

    # When: its immutable release commands are inspected.
    pytest_command = "uv run pytest --cov=custom_components.geofence_journal"
    coverage_options = "--cov-branch --cov-fail-under=95"
    commands = {
        "uv sync --all-groups --frozen",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run basedpyright",
        f"{pytest_command} {coverage_options}",
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
        "ghcr.io/hacs/action@sha256:3b5eaf821de267ede11dfdd3a792679dfdc72f52bdaf395422b39f0cfad27120",
        "ghcr.io/home-assistant/hassfest@sha256:5fc3c5d7df109d248a61acfb0a675b38b1e4dc3a202feb260a8e33696f9803e3",
    }
    required_commands = {
        'uv run python -m scripts.release check "${GITHUB_REF_NAME}"',
        "uv run python -m scripts.release build dist",
        'gh release create "${GITHUB_REF_NAME}" dist/geofence_journal.zip',
        "PRERELEASE: ${{ needs.verify.outputs.prerelease }}",
    }

    # Then: a verified artifact is the only input to a tag-only publication job.
    assert pinned_actions <= {action for action in pinned_actions if action in workflow}
    assert required_commands <= {
        command for command in required_commands if command in workflow
    }
    assert 'tags:\n      - "v*"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "needs:\n      - verify" in workflow
    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in workflow
    assert "contents: write" in workflow
    assert "GH_REPO: ${{ github.repository }}" in workflow
    assert "--prerelease" in workflow
    assert "--json isLatest" not in workflow
    assert "latestRelease{tagName}" in workflow
    assert 'test "${latest_tag}" != "${GITHUB_REF_NAME}"' in workflow
    assert "INPUT_IGNORE" not in workflow
    assert "INPUT_REPOSITORY" not in workflow
    assert workflow.index("-m scripts.release check") < workflow.index(
        "-m scripts.release build"
    )
    assert workflow.index("-m scripts.release build") < workflow.index(
        "gh release create"
    )


def test_validation_workflow_pins_official_validator_containers() -> None:
    # Given: the repository validation workflow.
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text("utf-8")

    # When: the external validators are inspected.
    expected_images = {
        "ghcr.io/hacs/action@sha256:3b5eaf821de267ede11dfdd3a792679dfdc72f52bdaf395422b39f0cfad27120",
        "ghcr.io/home-assistant/hassfest@sha256:5fc3c5d7df109d248a61acfb0a675b38b1e4dc3a202feb260a8e33696f9803e3",
    }

    # Then: both official validator images are immutable and scheduled.
    assert expected_images <= {image for image in expected_images if image in workflow}
    assert "INPUT_CATEGORY: integration" in workflow
    assert "INPUT_IGNORE" not in workflow
    assert "INPUT_REPOSITORY" not in workflow
    assert "schedule:" in workflow


def test_dependabot_monitors_actions_and_locked_python_dependencies() -> None:
    # Given: the repository-wide automated dependency update policy.
    policy = (ROOT / ".github" / "dependabot.yml").read_text("utf-8")

    # When / Then: both executable workflows and the uv lock stay monitored.
    assert 'package-ecosystem: "github-actions"' in policy
    assert 'package-ecosystem: "uv"' in policy
    assert policy.count('interval: "weekly"') == 2


def test_security_policy_routes_sensitive_reports_privately() -> None:
    policy = " ".join((ROOT / "SECURITY.md").read_text("utf-8").split()).lower()

    assert "/security/advisories/new" in policy
    assert "do not open a public issue" in policy
    assert "coordinates" in policy
    assert "database" in policy
