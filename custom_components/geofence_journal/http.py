"""Authenticated HTTP download adapter for CSV export artifacts."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, final

from aiohttp.hdrs import CONTENT_DISPOSITION
from aiohttp.web import FileResponse, Request, Response, StreamResponse
from homeassistant.components.http.decorators import require_admin
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .export import ExportArtifact, ExportRegistry

from .export import EXPORT_LIFETIME


async def async_cleanup_orphaned_exports(
    hass: HomeAssistant, registry: ExportRegistry
) -> int:
    """Delete stale restart artifacts without blocking HA's event loop."""
    return await hass.async_add_executor_job(registry.cleanup_orphaned_files)


@callback
def async_register_export_view(hass: HomeAssistant, registry: ExportRegistry) -> None:
    """Register the sole authenticated export download route."""
    hass.http.register_view(ExportDownloadView(registry))


@callback
def async_schedule_export_cleanup(
    hass: HomeAssistant,
    registry: ExportRegistry,
    artifact: ExportArtifact,
) -> Callable[[], None]:
    """Schedule unconditional artifact deletion at its 24-hour expiry."""

    async def remove_at_expiry(_: datetime) -> None:
        await hass.async_add_executor_job(registry.discard, artifact.export_id)

    return async_call_later(hass, EXPORT_LIFETIME, remove_at_expiry)


@final
class ExportDownloadView(HomeAssistantView):
    """Serve one unexpired CSV artifact to an authenticated administrator."""

    url = "/api/geofence_journal/export/{export_id}"
    name = "api:geofence_journal:export"
    requires_auth = True

    def __init__(self, registry: ExportRegistry) -> None:
        """Bind the opaque artifact registry."""
        self._registry = registry

    @require_admin
    async def get(
        self, request: Request, export_id: str
    ) -> StreamResponse | FileResponse | Response:
        """Return the file only while its opaque identifier remains valid."""
        hass: HomeAssistant = request.app[KEY_HASS]
        artifact = await hass.async_add_executor_job(self._registry.resolve, export_id)
        if artifact is None:
            return Response(status=HTTPStatus.NOT_FOUND)
        return FileResponse(
            artifact.path,
            headers={
                CONTENT_DISPOSITION: (
                    f'attachment; filename="geofence_journal_{artifact.export_id}.csv"'
                )
            },
        )
