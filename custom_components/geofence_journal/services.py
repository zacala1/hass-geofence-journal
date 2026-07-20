"""Typed Home Assistant service boundary for Geofence Journal."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final

from homeassistant.core import (
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from pydantic import ValidationError

from .const import DOMAIN
from .maintenance import (
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
    transition_event_payload,
)
from .service_dispatch import (
    EVENT_JOURNAL,
    SERVICE_ACTIONS,
    ServiceAction,
    ServicesBackend,
    async_dispatch_service,
    async_fire_journal_event,
)
from .storage.events import EventNotFoundError, MissingEventReferenceError
from .storage.maintenance import (
    CheckpointBusyError,
    PurgeConfirmationError,
    ResetConfirmationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from homeassistant.core import HomeAssistant


__all__: Final = (
    "EVENT_JOURNAL",
    "ServicesBackend",
    "UpsertJournalRequest",
    "UpsertPlaceRequest",
    "UpsertRuleRequest",
    "UpsertTrackerRequest",
    "async_fire_journal_event",
    "transition_event_payload",
)


async def async_register_services(
    hass: HomeAssistant, backend: ServicesBackend
) -> None:
    """Register the admin service surface once."""
    for action in SERVICE_ACTIONS:
        hass.services.async_register(
            DOMAIN,
            action.value,
            _service_handler(action, backend),
            supports_response=SupportsResponse.OPTIONAL,
        )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the service surface when the sole entry unloads."""
    for action in SERVICE_ACTIONS:
        hass.services.async_remove(DOMAIN, action.value)


def _service_handler(
    action: ServiceAction, backend: ServicesBackend
) -> Callable[[ServiceCall], Coroutine[None, None, ServiceResponse]]:
    async def handle(call: ServiceCall) -> ServiceResponse:
        await _require_admin(call)
        try:
            return await async_dispatch_service(action, call, backend)
        except ValidationError as error:
            count = error.error_count()
            detail = f"invalid service data ({count} validation error(s))"
            raise ServiceValidationError(detail) from error
        except (
            EventNotFoundError,
            MissingEventReferenceError,
            CheckpointBusyError,
            PurgeConfirmationError,
            ResetConfirmationError,
            sqlite3.IntegrityError,
        ) as error:
            raise ServiceValidationError(str(error)) from error

    return handle


async def _require_admin(call: ServiceCall) -> None:
    user_id = call.context.user_id
    user = None if user_id is None else await call.hass.auth.async_get_user(user_id)
    if user is None or not user.is_admin:
        raise Unauthorized(context=call.context, user_id=user_id)
