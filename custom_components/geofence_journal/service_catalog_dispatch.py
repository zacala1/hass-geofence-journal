"""Home Assistant response mapping for resource catalog actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .resource_catalog import (
    DeleteResourceRequest,
    GetResourceRequest,
    ListResourcesRequest,
    ResourceDeleteResponse,
    ResourceGetResponse,
    ResourceListResponse,
)
from .service_actions import ServiceAction

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall, ServiceResponse


class CatalogServicesBackend(Protocol):
    """Catalog-only subset accepted by the catalog dispatcher."""

    async def async_list_resources(
        self, request: ListResourcesRequest
    ) -> ResourceListResponse:
        """List selected resources."""
        ...

    async def async_get_resource(
        self, request: GetResourceRequest
    ) -> ResourceGetResponse:
        """Read one resource."""
        ...

    async def async_delete_resource(
        self, request: DeleteResourceRequest
    ) -> ResourceDeleteResponse:
        """Delete one confirmed resource."""
        ...


async def async_dispatch_catalog(
    action: ServiceAction,
    call: ServiceCall,
    backend: CatalogServicesBackend,
) -> ServiceResponse:
    """Parse, dispatch, and serialize one resource catalog action."""
    if action is ServiceAction.LIST_RESOURCES:
        response = await backend.async_list_resources(
            ListResourcesRequest.model_validate(call.data)
        )
        return {
            "resources": [item.model_dump(mode="json") for item in response.resources]
        }
    if action is ServiceAction.GET_RESOURCE:
        response = await backend.async_get_resource(
            GetResourceRequest.model_validate(call.data)
        )
        return {"resource": response.resource.model_dump(mode="json")}
    if action is ServiceAction.DELETE_RESOURCE:
        response = await backend.async_delete_resource(
            DeleteResourceRequest.model_validate(call.data)
        )
        return {
            "deleted": True,
            "resource_type": response.resource_type.value,
            "resource_id": response.resource_id,
        }
    raise RuntimeError(action)
