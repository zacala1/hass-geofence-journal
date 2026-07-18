from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Final, assert_never

import pytest
import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant.util import yaml as yaml_util
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

ROOT: Final = Path(__file__).parents[1]
SERVICES_PATH: Final = ROOT / "custom_components/geofence_journal/services.yaml"
TRANSLATIONS: Final = ROOT / "custom_components/geofence_journal/translations"
STRICT_MODEL_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type TranslationNode = str | dict[str, TranslationNode]


class ServiceField(BaseModel):
    model_config: ClassVar[ConfigDict] = STRICT_MODEL_CONFIG

    required: bool = False
    default: JsonValue = None
    example: JsonValue = None
    selector: dict[str, JsonValue]


class ServiceDescription(BaseModel):
    model_config: ClassVar[ConfigDict] = STRICT_MODEL_CONFIG

    fields: dict[str, ServiceField]


class TextDescription(BaseModel):
    model_config: ClassVar[ConfigDict] = STRICT_MODEL_CONFIG

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ActionTranslation(TextDescription):
    fields: dict[str, TextDescription]


SERVICE_ADAPTER: Final = TypeAdapter(dict[str, ServiceDescription])
TRANSLATION_ADAPTER: Final = TypeAdapter(dict[str, TranslationNode])
ACTION_ADAPTER: Final = TypeAdapter(dict[str, ActionTranslation])


def _names(*values: str) -> frozenset[str]:
    return frozenset(name for value in values for name in value.split())


def _paths(*values: str) -> frozenset[tuple[str, ...]]:
    return frozenset(tuple(value.split(".")) for value in values)


EXPECTED_FIELDS: Final[dict[str, frozenset[str]]] = {
    "upsert_tracker": _names("resource_id entity_id kind name enabled"),
    "upsert_place": _names(
        "resource_id name source_type zone_entity_id latitude longitude",
        "radius_meters exit_margin_meters enabled",
    ),
    "upsert_journal": _names("resource_id name enabled"),
    "upsert_rule": _names(
        "resource_id name tracker_id place_id journal_id",
        "enter_confirmation_seconds exit_confirmation_seconds cooldown_seconds",
        "max_gps_accuracy_meters enabled",
    ),
    "add_event": _names(
        "journal_id tracker_id place_id occurred_at latitude longitude accuracy_m note"
    ),
    "exclude_event": _names("event_id reason"),
    "restore_event": _names("event_id reason"),
    "export_journal": _names("journal_id start_at end_at include_coordinates"),
    "purge_events": _names("before journal_id dry_run confirm"),
    "compact_database": frozenset(),
    "reset_database": _names("confirmation"),
}
REQUIRED_FIELDS: Final[dict[str, frozenset[str]]] = {
    "upsert_tracker": _names("entity_id kind name"),
    "upsert_place": _names("name source_type"),
    "upsert_journal": _names("name"),
    "upsert_rule": _names("name tracker_id place_id journal_id"),
    "add_event": _names("journal_id tracker_id place_id occurred_at"),
    "exclude_event": _names("event_id"),
    "restore_event": _names("event_id"),
    "export_journal": _names("journal_id"),
    "purge_events": _names("before journal_id"),
    "compact_database": frozenset(),
    "reset_database": _names("confirmation"),
}
SELECTOR_BY_FIELD: Final[dict[str, str]] = {
    field: selector_type
    for selector_type, fields in (
        (
            "text",
            _names(
                "resource_id name tracker_id place_id journal_id note",
                "event_id reason confirmation",
            ),
        ),
        ("entity", _names("entity_id zone_entity_id")),
        ("select", _names("kind source_type")),
        ("boolean", _names("enabled include_coordinates dry_run confirm")),
        ("datetime", _names("occurred_at start_at end_at before")),
        (
            "number",
            _names(
                "latitude longitude radius_meters exit_margin_meters",
                "enter_confirmation_seconds exit_confirmation_seconds cooldown_seconds",
                "max_gps_accuracy_meters accuracy_m",
            ),
        ),
    )
    for field in fields
}


def _load_services() -> dict[str, ServiceDescription]:
    return SERVICE_ADAPTER.validate_python(yaml_util.load_yaml(SERVICES_PATH))


def _load_translation(language: str) -> dict[str, TranslationNode]:
    return TRANSLATION_ADAPTER.validate_json(
        (TRANSLATIONS / f"{language}.json").read_text("utf-8"), strict=True
    )


def _leaf_paths(
    node: TranslationNode, prefix: tuple[str, ...] = ()
) -> frozenset[tuple[str, ...]]:
    match node:
        case str():
            return frozenset({prefix})
        case dict():
            return frozenset(
                path
                for key, child in node.items()
                for path in _leaf_paths(child, (*prefix, key))
            )
        case unreachable:
            assert_never(unreachable)


def test_services_yaml_matches_the_public_request_contracts() -> None:
    # Given: the metadata boundary for the eleven implemented admin actions.
    # When: Home Assistant service metadata is parsed into its strict shape.
    services = _load_services()

    # Then: no generated/audit field or deferred action leaks into the UI contract.
    assert frozenset(services) == frozenset(EXPECTED_FIELDS)
    assert {
        action: frozenset(description.fields)
        for action, description in services.items()
    } == EXPECTED_FIELDS
    assert {
        action: frozenset(
            name for name, field in description.fields.items() if field.required
        )
        for action, description in services.items()
    } == REQUIRED_FIELDS


def test_service_fields_have_valid_and_contract_specific_selectors() -> None:
    # Given: every public service field and its expected Home Assistant input type.
    services = _load_services()

    # When: each selector is checked by Home Assistant's real selector validator.
    for description in services.values():
        for field_name, field in description.fields.items():
            assert selector.validate_selector(field.selector)
            assert frozenset(field.selector) == frozenset(
                {SELECTOR_BY_FIELD[field_name]}
            )

    # Then: selectors expose the enum/domain/range safety encoded by Python requests.
    tracker = services["upsert_tracker"].fields
    place = services["upsert_place"].fields
    assert tracker["entity_id"].selector["entity"] == {
        "filter": {"domain": ["person", "device_tracker"]}
    }
    assert tracker["kind"].selector["select"] == {
        "options": ["person", "device_tracker"],
        "translation_key": "tracker_kind",
    }
    assert place["source_type"].selector["select"] == {
        "options": ["coordinates", "ha_zone"],
        "translation_key": "place_source",
    }
    assert place["latitude"].selector["number"] == {
        "min": -90,
        "max": 90,
        "step": "any",
        "mode": "box",
    }
    assert place["longitude"].selector["number"] == {
        "min": -180,
        "max": 180,
        "step": "any",
        "mode": "box",
    }


def test_service_and_entity_translations_cover_the_metadata_contract() -> None:
    # Given: independently shipped English and Korean custom translations.
    catalogs = {language: _load_translation(language) for language in ("en", "ko")}

    # When: service translations are parsed into their documented shape.
    translated = {
        language: ACTION_ADAPTER.validate_python(catalog["services"], strict=True)
        for language, catalog in catalogs.items()
    }

    # Then: every action/field, selector option, and fixed entity has readable text.
    for actions in translated.values():
        assert frozenset(actions) == frozenset(EXPECTED_FIELDS)
        assert {
            action: frozenset(translation.fields)
            for action, translation in actions.items()
        } == EXPECTED_FIELDS
    for catalog in catalogs.values():
        assert _leaf_paths(catalog["entity"]) == _paths(
            "sensor.last_event.name", "binary_sensor.healthy.name"
        )
        assert _leaf_paths(catalog["selector"]) == _paths(
            "tracker_kind.options.person",
            "tracker_kind.options.device_tracker",
            "place_source.options.coordinates",
            "place_source.options.ha_zone",
        )


def test_english_and_korean_translation_shapes_are_identical() -> None:
    # Given: both complete custom-integration language catalogs.
    english = _load_translation("en")
    korean = _load_translation("ko")

    # When: localized leaf paths are collected without comparing their prose.
    paths = (_leaf_paths(english), _leaf_paths(korean))

    # Then: a missing or misplaced translation in either language is rejected.
    assert paths[0] == paths[1]


def test_home_assistant_rejects_a_malformed_selector() -> None:
    # Given: an adversarial slider selector without its required bounds.
    malformed: dict[str, JsonValue] = {"number": {"mode": "slider"}}

    # When / Then: Home Assistant's selector boundary rejects the metadata.
    with pytest.raises(vol.Invalid, match="min and max are required"):
        assert selector.validate_selector(malformed)


def test_translation_parser_rejects_a_non_string_leaf() -> None:
    # Given: an adversarial translation catalog with a boolean prose value.
    malformed = '{"services":{"reset_database":{"name":false}}}'

    # When / Then: strict translation parsing rejects the malformed leaf.
    with pytest.raises(ValidationError, match="valid string"):
        _ = TRANSLATION_ADAPTER.validate_json(malformed, strict=True)
