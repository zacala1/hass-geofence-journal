from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from custom_components.geofence_journal.const import DOMAIN
from custom_components.geofence_journal.models import TrackerKind
from custom_components.geofence_journal.resource_catalog import (
    DeleteResourceRequest,
    GetResourceRequest,
    ListResourcesRequest,
    ResourceDeleteResponse,
    ResourceGetResponse,
    ResourceListResponse,
    ResourceType,
    TrackerResourceItem,
)
from custom_components.geofence_journal.services import async_register_services
from homeassistant.core import Context

if TYPE_CHECKING:
    from custom_components.geofence_journal.service_dispatch import ServicesBackend
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockUser

RESOURCE_ID: Final = "00000000-0000-4000-8000-000000000001"
TRACKER_ITEM: Final = TrackerResourceItem(
    resource_id=RESOURCE_ID,
    name="Alice",
    enabled=True,
    entity_id="person.alice",
    kind=TrackerKind.PERSON,
)


class CatalogBackend:
    async def async_list_resources(
        self, request: ListResourcesRequest
    ) -> ResourceListResponse:
        assert request.resource_type is ResourceType.TRACKER
        return ResourceListResponse((TRACKER_ITEM,))

    async def async_get_resource(
        self, request: GetResourceRequest
    ) -> ResourceGetResponse:
        assert str(request.resource_id) == RESOURCE_ID
        return ResourceGetResponse(TRACKER_ITEM)

    async def async_delete_resource(
        self, request: DeleteResourceRequest
    ) -> ResourceDeleteResponse:
        assert request.confirm is True
        return ResourceDeleteResponse(ResourceType.TRACKER, RESOURCE_ID)


async def test_catalog_services_return_json_safe_typed_resources(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    backend = cast("ServicesBackend", cast("object", CatalogBackend()))
    await async_register_services(hass, backend)
    context = Context(user_id=hass_admin_user.id)

    listed = await hass.services.async_call(
        DOMAIN,
        "list_resources",
        {"resource_type": "tracker"},
        blocking=True,
        context=context,
        return_response=True,
    )
    fetched = await hass.services.async_call(
        DOMAIN,
        "get_resource",
        {"resource_type": "tracker", "resource_id": RESOURCE_ID},
        blocking=True,
        context=context,
        return_response=True,
    )
    deleted = await hass.services.async_call(
        DOMAIN,
        "delete_resource",
        {
            "resource_type": "tracker",
            "resource_id": RESOURCE_ID,
            "confirm": True,
        },
        blocking=True,
        context=context,
        return_response=True,
    )

    expected = TRACKER_ITEM.model_dump(mode="json")
    assert listed == {"resources": [expected]}
    assert fetched == {"resource": expected}
    assert deleted == {
        "deleted": True,
        "resource_type": "tracker",
        "resource_id": RESOURCE_ID,
    }
