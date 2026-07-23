from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from custom_components.geofence_journal.const import DOMAIN
from custom_components.geofence_journal.services import async_register_services
from custom_components.geofence_journal.storage.maintenance import PurgeResult
from homeassistant.core import Context

if TYPE_CHECKING:
    from custom_components.geofence_journal.retention import PurgeRetentionRequest
    from custom_components.geofence_journal.service_dispatch import ServicesBackend
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockUser

JOURNAL_ID: Final = "00000000-0000-4000-8000-000000000003"


class RetentionBackend:
    async def async_purge_retention(
        self, request: PurgeRetentionRequest
    ) -> PurgeResult:
        assert request.dry_run is True
        assert request.confirm is False
        return PurgeResult(2, 1, 0, 0, dry_run=True)


async def test_retention_service_returns_standard_purge_counts(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    backend = cast("ServicesBackend", cast("object", RetentionBackend()))
    await async_register_services(hass, backend)

    response = await hass.services.async_call(
        DOMAIN,
        "purge_retention",
        {"journal_id": JOURNAL_ID},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
        return_response=True,
    )

    assert response == {
        "matched_events": 2,
        "matched_revisions": 1,
        "deleted_events": 0,
        "deleted_revisions": 0,
        "dry_run": True,
    }
