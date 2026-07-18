from uuid import UUID

import pytest
from custom_components.geofence_journal.maintenance import (
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
)
from custom_components.geofence_journal.models import PlaceKind, TrackerKind
from pydantic import ValidationError

RESOURCE_ID = UUID("00000000-0000-4000-8000-000000000001")


def test_tracker_request_accepts_matching_supported_domain() -> None:
    # Given: a person entity and the matching closed tracker kind.
    entity_id = "person.alice"

    # When: service data crosses the typed request boundary.
    request = UpsertTrackerRequest(
        resource_id=RESOURCE_ID,
        entity_id=entity_id,
        kind=TrackerKind.PERSON,
        name="Alice",
    )

    # Then: the stable identifier and normalized entity survive parsing.
    assert (request.resource_id, request.entity_id) == (RESOURCE_ID, entity_id)


@pytest.mark.parametrize(
    ("entity_id", "kind"),
    [
        ("sensor.alice", TrackerKind.PERSON),
        ("person.alice", TrackerKind.DEVICE_TRACKER),
        ("malformed", TrackerKind.PERSON),
    ],
)
def test_tracker_request_rejects_cross_domain_entity(
    entity_id: str, kind: TrackerKind
) -> None:
    # Given: an entity ID outside the selected supported domain.
    service_data = {"entity_id": entity_id, "kind": kind, "name": "Alice"}

    # When / Then: boundary parsing rejects it before storage is called.
    with pytest.raises(ValidationError):
        _ = UpsertTrackerRequest.model_validate(service_data)


def test_coordinate_place_requires_complete_geometry() -> None:
    # Given: coordinate source data with its complete shape.
    service_data = {
        "name": "Home",
        "source_type": PlaceKind.COORDINATE,
        "latitude": 37.5,
        "longitude": 127.0,
        "radius_meters": 100.0,
        "exit_margin_meters": 25.0,
    }

    # When: the place boundary is parsed.
    request = UpsertPlaceRequest.model_validate(service_data)

    # Then: the place-owned exit margin remains explicit.
    assert request.exit_margin_meters == 25.0


@pytest.mark.parametrize(
    "service_data",
    [
        {
            "name": "Bad latitude",
            "source_type": "coordinates",
            "latitude": 95,
            "longitude": 0,
            "radius_meters": 100,
        },
        {
            "name": "Bad radius",
            "source_type": "coordinates",
            "latitude": 0,
            "longitude": 0,
            "radius_meters": -1,
        },
        {"name": "Missing zone", "source_type": "ha_zone"},
        {
            "name": "Empty zone object",
            "source_type": "ha_zone",
            "zone_entity_id": "zone.",
        },
        {
            "name": "Mixed shape",
            "source_type": "ha_zone",
            "zone_entity_id": "zone.home",
            "latitude": 0,
        },
    ],
)
def test_place_request_rejects_invalid_source_shape(
    service_data: dict[str, str | int],
) -> None:
    # Given: an impossible coordinate or zone place shape.

    # When / Then: one boundary parse rejects it without a partial model.
    with pytest.raises(ValidationError):
        _ = UpsertPlaceRequest.model_validate(service_data)


@pytest.mark.parametrize(
    "field",
    [
        "enter_confirmation_seconds",
        "exit_confirmation_seconds",
        "cooldown_seconds",
    ],
)
def test_rule_request_rejects_negative_timings(field: str) -> None:
    # Given: otherwise linked UUID resources and one negative duration.
    service_data = {
        "name": "Rule",
        "tracker_id": RESOURCE_ID,
        "place_id": RESOURCE_ID,
        "journal_id": RESOURCE_ID,
        field: -1,
    }

    # When / Then: parsing fails before any reference query or write.
    with pytest.raises(ValidationError):
        _ = UpsertRuleRequest.model_validate(service_data)
