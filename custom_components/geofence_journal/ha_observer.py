"""Home Assistant observer for newly committed geofence transitions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, final

from .management_events import transition_event_payload
from .services import async_fire_journal_event

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from .storage.records import RuntimeStateRecord, TransitionResult
    from .storage.resources import ConfiguredResources


@final
class HomeAssistantTransitionObserver:
    """Emit one coordinate-free HA event and update entity state."""

    def __init__(
        self,
        hass: HomeAssistant,
        resources: ConfiguredResources,
        record_transition: Callable[[datetime], None],
    ) -> None:
        """Bind one rule's names and identifiers to HA notification sinks."""
        self._hass = hass
        self._resources = resources
        self._record_transition = record_transition

    async def on_transition(
        self, result: TransitionResult, state: RuntimeStateRecord
    ) -> None:
        """Publish only a newly committed transition without coordinates."""
        payload = transition_event_payload(result, state, self._resources)
        async_fire_journal_event(self._hass, payload)
        self._record_transition(datetime.fromisoformat(payload.timestamp))
