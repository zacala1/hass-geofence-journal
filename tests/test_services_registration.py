from __future__ import annotations

import json
from traceback import format_exception
from typing import TYPE_CHECKING, Final
from uuid import UUID

import pytest
from custom_components.geofence_journal.const import DOMAIN
from custom_components.geofence_journal.maintenance import (
    ResourceResponse,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
)
from custom_components.geofence_journal.resource_catalog import (
    DeleteResourceRequest,
    GetResourceRequest,
    ListResourcesRequest,
    ResourceDeleteResponse,
    ResourceGetResponse,
    ResourceListResponse,
)
from custom_components.geofence_journal.services import (
    async_register_services,
    async_unregister_services,
)
from homeassistant.core import Context, SupportsResponse
from homeassistant.exceptions import ServiceValidationError, Unauthorized

if TYPE_CHECKING:
    from custom_components.geofence_journal.export import ExportRequest
    from custom_components.geofence_journal.maintenance import (
        AddEventRequest,
        EventResponse,
        ExcludeEventRequest,
        ExportResponse,
        PurgeEventsRequest,
        ResetDatabaseRequest,
        RestoreEventRequest,
    )
    from custom_components.geofence_journal.storage.maintenance import (
        CompactResult,
        PurgeResult,
        ResetResult,
    )
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockUser

RESOURCE_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
EXPECTED_SERVICES: Final = {
    "upsert_tracker",
    "upsert_place",
    "upsert_journal",
    "upsert_rule",
    "list_resources",
    "get_resource",
    "delete_resource",
    "add_event",
    "exclude_event",
    "restore_event",
    "export_journal",
    "purge_events",
    "compact_database",
    "reset_database",
}


class RegistrationBackend:
    def __init__(self) -> None:
        self.tracker_request: UpsertTrackerRequest | None = None

    async def async_upsert_tracker(
        self, request: UpsertTrackerRequest
    ) -> ResourceResponse:
        self.tracker_request = request
        return ResourceResponse(resource_id=request.resource_id or RESOURCE_ID)

    async def async_upsert_place(self, request: UpsertPlaceRequest) -> ResourceResponse:
        return ResourceResponse(resource_id=request.resource_id or RESOURCE_ID)

    async def async_upsert_journal(
        self, request: UpsertJournalRequest
    ) -> ResourceResponse:
        return ResourceResponse(resource_id=request.resource_id or RESOURCE_ID)

    async def async_upsert_rule(self, request: UpsertRuleRequest) -> ResourceResponse:
        return ResourceResponse(resource_id=request.resource_id or RESOURCE_ID)

    async def async_list_resources(
        self, request: ListResourcesRequest
    ) -> ResourceListResponse:
        _ = request
        return ResourceListResponse(())

    async def async_get_resource(
        self, request: GetResourceRequest
    ) -> ResourceGetResponse:
        _ = request
        raise NotImplementedError

    async def async_delete_resource(
        self, request: DeleteResourceRequest
    ) -> ResourceDeleteResponse:
        _ = request
        raise NotImplementedError

    async def async_add_event(
        self, request: AddEventRequest, user_id: str | None
    ) -> EventResponse:
        _ = (request, user_id)
        raise NotImplementedError

    async def async_exclude_event(
        self, request: ExcludeEventRequest, user_id: str | None
    ) -> EventResponse:
        _ = (request, user_id)
        raise NotImplementedError

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
async def test_register_services_exposes_only_the_admin_management_surface(
    hass: HomeAssistant,
) -> None:
    # Given: a manager-owned backend injected into an otherwise clean registry.
    backend = RegistrationBackend()

    # When: Task 5 invokes the public registration hook.
    await async_register_services(hass, backend)

    # Then: every specified action exists and explicitly supports responses.
    registered = hass.services.async_services_for_domain(DOMAIN)
    assert set(registered) == EXPECTED_SERVICES
    assert all(
        service.supports_response is SupportsResponse.OPTIONAL
        for service in registered.values()
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unregister_services_removes_every_action(hass: HomeAssistant) -> None:
    # Given: the sole entry registered the management surface.
    await async_register_services(hass, RegistrationBackend())

    # When: Task 5 unloads that sole entry.
    await async_unregister_services(hass)

    # Then: no stale callable service remains.
    assert hass.services.async_services_for_domain(DOMAIN) == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_admin_upsert_returns_json_identifier_and_typed_request(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    # Given: an admin context and an injected backend recording typed input.
    backend = RegistrationBackend()
    await async_register_services(hass, backend)

    # When: the service is invoked through Home Assistant's real registry.
    response = await hass.services.async_call(
        DOMAIN,
        "upsert_tracker",
        {"entity_id": "person.alice", "kind": "person", "name": "Alice"},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
        return_response=True,
    )

    # Then: the handler parsed once and returned JSON-safe data.
    assert response == {"resource_id": str(RESOURCE_ID)}
    assert json.loads(json.dumps(response)) == response
    assert backend.tracker_request is not None
    assert backend.tracker_request.entity_id == "person.alice"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_non_admin_is_rejected_before_backend_invocation(
    hass: HomeAssistant, hass_read_only_user: MockUser
) -> None:
    # Given: a non-admin context and an otherwise valid request.
    backend = RegistrationBackend()
    await async_register_services(hass, backend)

    # When / Then: authorization fails before parsing or persistence.
    with pytest.raises(Unauthorized):
        _ = await hass.services.async_call(
            DOMAIN,
            "upsert_tracker",
            {"entity_id": "person.alice", "kind": "person", "name": "Alice"},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
            return_response=True,
        )
    assert backend.tracker_request is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_malformed_service_data_never_reaches_backend(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    # Given: an admin call whose tracker domain conflicts with its kind.
    backend = RegistrationBackend()
    await async_register_services(hass, backend)

    # When / Then: the typed boundary raises HA's validation error atomically.
    with pytest.raises(ServiceValidationError):
        _ = await hass.services.async_call(
            DOMAIN,
            "upsert_tracker",
            {"entity_id": "sensor.alice", "kind": "person", "name": "Alice"},
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
            return_response=True,
        )
    assert backend.tracker_request is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_validation_error_does_not_echo_private_service_input(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    backend = RegistrationBackend()
    await async_register_services(hass, backend)
    private_note = "private-home-coordinate-note"
    private_latitude = 123.456789

    with pytest.raises(ServiceValidationError) as raised:
        _ = await hass.services.async_call(
            DOMAIN,
            "add_event",
            {
                "journal_id": str(RESOURCE_ID),
                "tracker_id": str(RESOURCE_ID),
                "place_id": str(RESOURCE_ID),
                "occurred_at": "2026-07-18T12:00:00Z",
                "latitude": private_latitude,
                "longitude": 127.0,
                "note": private_note,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
            return_response=True,
        )

    message = str(raised.value)
    assert "invalid service data" in message
    assert private_note not in message
    assert str(private_latitude) not in message
    traceback_text = "".join(format_exception(raised.value))
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert private_note not in traceback_text
    assert str(private_latitude) not in traceback_text
