from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from custom_components.geofence_journal import services
from custom_components.geofence_journal.const import DOMAIN
from custom_components.geofence_journal.maintenance import (
    AddEventRequest,
    EventResponse,
    ExcludeEventRequest,
    JournalEventPayload,
    PurgeEventsRequest,
    ResetDatabaseRequest,
    ResourceResponse,
    RestoreEventRequest,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
    journal_event_data,
)
from custom_components.geofence_journal.services import async_register_services
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import MockUser, async_capture_events

if TYPE_CHECKING:
    from custom_components.geofence_journal.export import ExportRequest
    from custom_components.geofence_journal.maintenance import ExportResponse
    from custom_components.geofence_journal.storage.maintenance import (
        CompactResult,
        PurgeResult,
        ResetResult,
    )
    from homeassistant.core import HomeAssistant

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
EVENT_ID: Final = "00000000-0000-4000-8000-000000000010"
PAYLOAD: Final = JournalEventPayload(
    event_id=EVENT_ID,
    journal_id="00000000-0000-4000-8000-000000000003",
    journal_name="Presence",
    rule_id=None,
    tracker_id="00000000-0000-4000-8000-000000000001",
    tracker_name="Alice",
    place_id="00000000-0000-4000-8000-000000000002",
    place_name="Home",
    event_type="manual",
    status="confirmed",
    timestamp="2026-07-18T12:00:00Z",
)


class EventBackend:
    def __init__(self, *, changed: bool = True) -> None:
        self.changed: bool = changed
        self.add_request: AddEventRequest | None = None

    async def async_upsert_tracker(
        self, request: UpsertTrackerRequest
    ) -> ResourceResponse:
        _ = request
        raise NotImplementedError

    async def async_upsert_place(self, request: UpsertPlaceRequest) -> ResourceResponse:
        _ = request
        raise NotImplementedError

    async def async_upsert_journal(
        self, request: UpsertJournalRequest
    ) -> ResourceResponse:
        _ = request
        raise NotImplementedError

    async def async_upsert_rule(self, request: UpsertRuleRequest) -> ResourceResponse:
        _ = request
        raise NotImplementedError

    async def async_add_event(
        self, request: AddEventRequest, user_id: str | None
    ) -> EventResponse:
        self.add_request = request
        assert user_id is not None
        return EventResponse(self.changed, PAYLOAD)

    async def async_exclude_event(
        self, request: ExcludeEventRequest, user_id: str | None
    ) -> EventResponse:
        _ = (request, user_id)
        return EventResponse(self.changed, PAYLOAD)

    async def async_restore_event(
        self, request: RestoreEventRequest, user_id: str | None
    ) -> EventResponse:
        _ = (request, user_id)
        raise NotImplementedError

    async def async_export_journal(self, request: ExportRequest) -> ExportResponse:
        _ = request
        raise NotImplementedError

    async def async_purge_events(self, request: PurgeEventsRequest) -> PurgeResult:
        _ = request
        raise NotImplementedError

    async def async_compact_database(self) -> CompactResult:
        raise NotImplementedError

    async def async_reset_database(self, request: ResetDatabaseRequest) -> ResetResult:
        _ = request
        raise NotImplementedError


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_manual_service_returns_json_and_fires_coordinate_free_event(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    # Given: a real HA registry, an administrator, and a created event response.
    backend = EventBackend()
    await async_register_services(hass, backend)
    events = async_capture_events(hass, "geofence_journal_event")

    # When: the public manual-event service is called.
    response = await hass.services.async_call(
        DOMAIN,
        "add_event",
        {
            "journal_id": PAYLOAD.journal_id,
            "tracker_id": PAYLOAD.tracker_id,
            "place_id": PAYLOAD.place_id,
            "occurred_at": NOW.isoformat(),
        },
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
        return_response=True,
    )

    # Then: one JSON-safe response and event carry names but no coordinates.
    assert json.loads(json.dumps(response)) == response
    assert response == {
        "changed": True,
        "payload": journal_event_data(PAYLOAD),
    }
    assert [event.data for event in events] == [journal_event_data(PAYLOAD)]
    assert all("latitude" not in event.data for event in events)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unchanged_exclusion_does_not_fire_a_bus_event(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    # Given: an idempotent event mutation returned by the backend.
    await async_register_services(hass, EventBackend(changed=False))
    events = async_capture_events(hass, "geofence_journal_event")

    # When: exclusion requests the already-current status.
    response = await hass.services.async_call(
        DOMAIN,
        "exclude_event",
        {"event_id": EVENT_ID, "reason": "noise"},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
        return_response=True,
    )

    # Then: the response reports no change and no duplicate event is emitted.
    assert response is not None
    assert response["changed"] is False
    assert events == []


def test_service_module_exposes_confirmed_transition_helpers() -> None:
    # Given: Task 5 owns the runtime observer but not payload construction.
    required = {"async_fire_journal_event", "transition_event_payload"}

    # When / Then: the coordinate-free helper seam is public.
    assert required <= set(dir(services))


def test_add_event_boundary_rejects_naive_time_and_partial_coordinates() -> None:
    # Given: malformed public manual-event shapes.
    base = {
        "journal_id": PAYLOAD.journal_id,
        "tracker_id": PAYLOAD.tracker_id,
        "place_id": PAYLOAD.place_id,
    }

    # When / Then: neither shape crosses into a backend request.
    with pytest.raises(ValueError, match="timezone-aware"):
        _ = AddEventRequest.model_validate({**base, "occurred_at": "2026-07-18"})
    with pytest.raises(ValueError, match="required together"):
        _ = AddEventRequest.model_validate(
            {**base, "occurred_at": NOW, "latitude": 37.5}
        )
