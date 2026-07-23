"""Home Assistant Repairs issue lifecycle for database health."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, assert_never

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .entity_state import (
    DatabaseErrorEntityState,
    EntityStateProvider,
    HealthyEntityState,
    UnloadedEntityState,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

DATABASE_ISSUE_ID: Final = "database_unavailable"


@callback
def async_subscribe_database_issue(
    hass: HomeAssistant,
    provider: EntityStateProvider,
) -> Callable[[], None]:
    """Mirror provider health into one idempotent Repairs issue."""

    @callback
    def update() -> None:
        match provider.entity_state:
            case DatabaseErrorEntityState():
                ir.async_create_issue(
                    hass,
                    DOMAIN,
                    DATABASE_ISSUE_ID,
                    is_fixable=False,
                    is_persistent=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="database_unavailable",
                )
            case HealthyEntityState():
                ir.async_delete_issue(hass, DOMAIN, DATABASE_ISSUE_ID)
            case UnloadedEntityState():
                pass
            case unreachable:
                assert_never(unreachable)

    unsubscribe = provider.async_subscribe_entity_state(update)
    update()
    return unsubscribe
