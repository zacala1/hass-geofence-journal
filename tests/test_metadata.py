from pathlib import Path
from typing import Final, TypedDict

from custom_components.geofence_journal.const import DOMAIN, TITLE, VERSION
from pydantic import TypeAdapter

ROOT: Final = Path(__file__).parents[1]
INTEGRATION: Final = ROOT / "custom_components" / DOMAIN


class ManifestIdentity(TypedDict):
    domain: str
    name: str
    version: str


class HacsIdentity(TypedDict):
    name: str


class ConfigData(TypedDict):
    store_coordinates: str
    enter_confirmation_seconds: str
    exit_confirmation_seconds: str
    cooldown_seconds: str
    exit_margin_meters: str
    max_gps_accuracy_meters: str
    database_path: str


class UserStep(TypedDict):
    data: ConfigData


class ConfigSteps(TypedDict):
    user: UserStep


class TranslationConfig(TypedDict):
    step: ConfigSteps


class TranslationMetadata(TypedDict):
    config: TranslationConfig


def test_metadata_uses_one_domain_and_release_identity() -> None:
    # Given: repository and integration metadata files.
    manifest = TypeAdapter(ManifestIdentity).validate_json(
        (INTEGRATION / "manifest.json").read_text("utf-8")
    )
    hacs = TypeAdapter(HacsIdentity).validate_json(
        (ROOT / "hacs.json").read_text("utf-8")
    )

    # When: their stable identities are compared.
    identity = (manifest["domain"], manifest["name"], manifest["version"])

    # Then: package, display title, release, and HACS metadata agree.
    assert identity == (DOMAIN, TITLE, VERSION)
    assert hacs["name"] == TITLE
    assert INTEGRATION.name == DOMAIN


def test_translation_files_expose_every_config_field() -> None:
    # Given: the complete English and Korean custom-integration translations.
    translations = [
        TypeAdapter(TranslationMetadata).validate_json(
            (INTEGRATION / "translations" / language).read_text("utf-8")
        )
        for language in ("en.json", "ko.json")
    ]

    # When: the user-step field keys are read.
    fields = [set(item["config"]["step"]["user"]["data"]) for item in translations]

    # Then: translations contain exactly the frozen settings surface.
    assert (
        fields[0]
        == fields[1]
        == {
            "store_coordinates",
            "enter_confirmation_seconds",
            "exit_confirmation_seconds",
            "cooldown_seconds",
            "exit_margin_meters",
            "max_gps_accuracy_meters",
            "database_path",
        }
    )
