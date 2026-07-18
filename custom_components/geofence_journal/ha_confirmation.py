"""Live Home Assistant confirmation-deadline re-evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never, final

from .geofence import EvaluatedObservation, HaversineDistance
from .listener import HomeAssistantZoneLookup, evaluate_ha_tracker_state
from .location import IgnoredObservation

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import PresenceState
    from .storage.records import RuntimeStateRecord
    from .storage.resources import ConfiguredResources


@final
class HomeAssistantConfirmationEvaluator:
    """Re-read tracker state and dynamic Zone geometry at a pending deadline."""

    def __init__(self, hass: HomeAssistant, resources: ConfiguredResources) -> None:
        """Bind one enabled rule to Home Assistant's current state machine."""
        self._hass = hass
        self._resources = resources
        self._zones = HomeAssistantZoneLookup(hass)
        self._distance = HaversineDistance()

    async def async_evaluate(self, state: RuntimeStateRecord) -> PresenceState | None:
        """Return a fresh presence result without persisting raw coordinates."""
        tracker = self._hass.states.get(self._resources.tracker.entity_id)
        if tracker is None:
            return None
        evaluated = evaluate_ha_tracker_state(
            tracker,
            self._resources,
            state,
            self._zones,
            self._distance,
        )
        match evaluated:
            case IgnoredObservation():
                return None
            case EvaluatedObservation(presence=presence):
                return presence
            case unreachable:
                assert_never(unreachable)
