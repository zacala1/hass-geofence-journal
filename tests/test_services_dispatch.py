from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final
from uuid import UUID

import pytest
from custom_components.geofence_journal.const import DOMAIN
from custom_components.geofence_journal.maintenance import (
    AddEventRequest,
    EventResponse,
    ExcludeEventRequest,
    ExportResponse,
    JournalEventPayload,
    PurgeEventsRequest,
    ResetDatabaseRequest,
    ResourceResponse,
    RestoreEventRequest,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
)
from custom_components.geofence_journal.management_events import journal_event_data
from custom_components.geofence_journal.services import async_register_services
from custom_components.geofence_journal.storage.maintenance import (
    CheckpointBusyError,
    CompactResult,
    PurgeResult,
    ResetConfirmationError,
    ResetResult,
)
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from pytest_homeassistant_custom_component.common import MockUser, async_capture_events

if TYPE_CHECKING:
    from custom_components.geofence_journal import resource_catalog
    from custom_components.geofence_journal.export import ExportRequest
    from custom_components.geofence_journal.retention import PurgeRetentionRequest
    from homeassistant.core import HomeAssistant, ServiceResponse

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
RESOURCE_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
JOURNAL_ID: Final = "00000000-0000-4000-8000-000000000003"
EVENT_ID: Final = "00000000-0000-4000-8000-000000000010"
RESET_PHRASE: Final = "DELETE ALL GEOFENCE JOURNAL DATA"
PAYLOAD: Final = JournalEventPayload(
    event_id=EVENT_ID,
    journal_id=JOURNAL_ID,
    journal_name="Presence",
    rule_id=None,
    tracker_id=str(RESOURCE_ID),
    tracker_name="Alice",
    place_id="00000000-0000-4000-8000-000000000002",
    place_name="Home",
    event_type="manual",
    status="confirmed",
    timestamp="2026-07-18T12:00:00Z",
)


@final
class DispatchBackend:
    def __init__(
        self, *, compact_error: bool = False, reset_error: bool = False
    ) -> None:
        self.calls: list[str] = []
        self._compact_error = compact_error
        self._reset_error = reset_error

    async def async_upsert_tracker(
        self, request: UpsertTrackerRequest
    ) -> ResourceResponse:
        _ = request
        return ResourceResponse(resource_id=RESOURCE_ID)

    async def async_upsert_place(self, request: UpsertPlaceRequest) -> ResourceResponse:
        _ = request
        return ResourceResponse(resource_id=RESOURCE_ID)

    async def async_upsert_journal(
        self, request: UpsertJournalRequest
    ) -> ResourceResponse:
        _ = request
        return ResourceResponse(resource_id=RESOURCE_ID)

    async def async_upsert_rule(self, request: UpsertRuleRequest) -> ResourceResponse:
        _ = request
        return ResourceResponse(resource_id=RESOURCE_ID)

    async def async_list_resources(
        self, request: resource_catalog.ListResourcesRequest
    ) -> resource_catalog.ResourceListResponse:
        _ = request
        raise NotImplementedError

    async def async_get_resource(
        self, request: resource_catalog.GetResourceRequest
    ) -> resource_catalog.ResourceGetResponse:
        _ = request
        raise NotImplementedError

    async def async_delete_resource(
        self, request: resource_catalog.DeleteResourceRequest
    ) -> resource_catalog.ResourceDeleteResponse:
        _ = request
        raise NotImplementedError

    async def async_add_event(
        self, request: AddEventRequest, user_id: str | None
    ) -> EventResponse:
        _ = (request, user_id)
        return EventResponse(changed=False, payload=PAYLOAD)

    async def async_exclude_event(
        self, request: ExcludeEventRequest, user_id: str | None
    ) -> EventResponse:
        _ = (request, user_id)
        return EventResponse(changed=False, payload=PAYLOAD)

    async def async_restore_event(
        self, request: RestoreEventRequest, user_id: str | None
    ) -> EventResponse:
        _ = (request, user_id)
        self.calls.append("restore")
        return EventResponse(changed=True, payload=PAYLOAD)

    async def async_export_journal(self, request: ExportRequest) -> ExportResponse:
        _ = request
        self.calls.append("export")
        return ExportResponse("/api/export/opaque", "2026-07-19T12:00:00Z", 7)

    async def async_purge_events(self, request: PurgeEventsRequest) -> PurgeResult:
        _ = request
        self.calls.append("purge")
        return PurgeResult(
            matched_events=4,
            matched_revisions=3,
            deleted_events=0,
            deleted_revisions=0,
            dry_run=True,
        )

    async def async_purge_retention(
        self, request: PurgeRetentionRequest
    ) -> PurgeResult:
        return PurgeResult(0, 0, 0, 0, dry_run=request.dry_run)

    async def async_compact_database(self) -> CompactResult:
        self.calls.append("compact")
        if self._compact_error:
            raise CheckpointBusyError
        return CompactResult(
            database_bytes_before=100,
            database_bytes_after=80,
            wal_bytes_before=20,
            wal_bytes_after=0,
            checkpoint_log_pages=2,
            checkpointed_pages=2,
            checkpoint_busy=False,
        )

    async def async_reset_database(self, request: ResetDatabaseRequest) -> ResetResult:
        _ = request
        self.calls.append("reset")
        if self._reset_error:
            raise ResetConfirmationError
        return ResetResult(1, 2, 3, 4, 5, 6, 7, 1)


async def _admin_call(
    hass: HomeAssistant,
    user: MockUser,
    action: str,
    data: dict[str, bool | str],
) -> ServiceResponse:
    return await hass.services.async_call(
        DOMAIN,
        action,
        data,
        blocking=True,
        context=Context(user_id=user.id),
        return_response=True,
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_admin_dispatch_maps_five_responses_and_restore_bus_event(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    backend = DispatchBackend()
    await async_register_services(hass, backend)
    events = async_capture_events(hass, "geofence_journal_event")

    restored = await _admin_call(
        hass, hass_admin_user, "restore_event", {"event_id": EVENT_ID}
    )
    exported = await _admin_call(
        hass, hass_admin_user, "export_journal", {"journal_id": JOURNAL_ID}
    )
    purged = await _admin_call(
        hass,
        hass_admin_user,
        "purge_events",
        {"journal_id": JOURNAL_ID, "before": NOW.isoformat()},
    )
    compacted = await _admin_call(hass, hass_admin_user, "compact_database", {})
    reset = await _admin_call(
        hass, hass_admin_user, "reset_database", {"confirmation": RESET_PHRASE}
    )

    assert restored == {"changed": True, "payload": journal_event_data(PAYLOAD)}
    assert exported == {
        "url": "/api/export/opaque",
        "expires_at": "2026-07-19T12:00:00Z",
        "count": 7,
    }
    assert purged == {
        "matched_events": 4,
        "matched_revisions": 3,
        "deleted_events": 0,
        "deleted_revisions": 0,
        "dry_run": True,
    }
    assert compacted == {
        "database_bytes_before": 100,
        "database_bytes_after": 80,
        "wal_bytes_before": 20,
        "wal_bytes_after": 0,
        "checkpoint_log_pages": 2,
        "checkpointed_pages": 2,
        "checkpoint_busy": False,
    }
    assert reset == {
        "deleted_trackers": 1,
        "deleted_places": 2,
        "deleted_journals": 3,
        "deleted_rules": 4,
        "deleted_events": 5,
        "deleted_revisions": 6,
        "deleted_runtime_states": 7,
        "schema_version": 1,
    }
    assert backend.calls == ["restore", "export", "purge", "compact", "reset"]
    assert [event.data for event in events] == [journal_event_data(PAYLOAD)]
    assert not {"latitude", "longitude", "accuracy_m"} & set(events[0].data)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reset_rejects_wrong_phrase_and_non_admin_before_backend(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
    hass_read_only_user: MockUser,
) -> None:
    backend = DispatchBackend()
    await async_register_services(hass, backend)

    with pytest.raises(ServiceValidationError):
        _ = await _admin_call(
            hass, hass_admin_user, "reset_database", {"confirmation": "DELETE"}
        )
    with pytest.raises(Unauthorized):
        _ = await _admin_call(
            hass, hass_read_only_user, "reset_database", {"confirmation": RESET_PHRASE}
        )

    assert backend.calls == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maintenance_failures_are_mapped_to_ha_validation_errors(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    backend = DispatchBackend(compact_error=True, reset_error=True)
    await async_register_services(hass, backend)

    with pytest.raises(ServiceValidationError, match="checkpoint remained busy"):
        _ = await _admin_call(hass, hass_admin_user, "compact_database", {})
    with pytest.raises(ServiceValidationError, match="reset requires exact phrase"):
        _ = await _admin_call(
            hass, hass_admin_user, "reset_database", {"confirmation": RESET_PHRASE}
        )

    assert backend.calls == ["compact", "reset"]
