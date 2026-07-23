from __future__ import annotations

from typing import Final

import pytest
from custom_components.geofence_journal.maintenance import (
    AddEventRequest,
    ExcludeEventRequest,
    ServiceRequest,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
)
from pydantic import ValidationError

UUID_1: Final = "00000000-0000-4000-8000-000000000001"
UUID_2: Final = "00000000-0000-4000-8000-000000000002"
UUID_3: Final = "00000000-0000-4000-8000-000000000003"


@pytest.mark.parametrize(
    ("request_type", "data"),
    [
        (
            UpsertTrackerRequest,
            {
                "entity_id": "person.alice",
                "kind": "person",
                "name": "x" * 129,
            },
        ),
        (
            UpsertPlaceRequest,
            {
                "name": "Home",
                "source_type": "coordinates",
                "latitude": 0,
                "longitude": 0,
                "radius_meters": 1_000_001,
            },
        ),
        (
            UpsertJournalRequest,
            {
                "name": "Presence",
                "retention_days": 0,
            },
        ),
        (
            UpsertRuleRequest,
            {
                "name": "Rule",
                "tracker_id": UUID_1,
                "place_id": UUID_2,
                "journal_id": UUID_3,
                "enter_confirmation_seconds": 86_401,
            },
        ),
        (
            UpsertRuleRequest,
            {
                "name": "Rule",
                "tracker_id": UUID_1,
                "place_id": UUID_2,
                "journal_id": UUID_3,
                "cooldown_seconds": 604_801,
            },
        ),
        (
            AddEventRequest,
            {
                "journal_id": UUID_3,
                "tracker_id": UUID_1,
                "place_id": UUID_2,
                "occurred_at": "2026-07-23T12:00:00Z",
                "note": "x" * 4_097,
            },
        ),
        (
            ExcludeEventRequest,
            {
                "event_id": UUID_1,
                "reason": "x" * 513,
            },
        ),
    ],
)
def test_service_requests_reject_unbounded_operational_values(
    request_type: type[ServiceRequest],
    data: dict[str, float | int | str],
) -> None:
    with pytest.raises(ValidationError):
        _ = request_type.model_validate(data)
