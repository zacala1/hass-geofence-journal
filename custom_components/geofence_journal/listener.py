"""Home Assistant tracker listener and boundary normalization."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final

import anyio
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.helpers.event import async_track_state_change_event
from pydantic import ConfigDict, TypeAdapter, ValidationError

from .geofence import (
    EvaluatedObservation,
    EvaluationThresholds,
    HaversineDistance,
    evaluate_geofence,
)
from .location import (
    IgnoredObservation,
    IgnoreReason,
    RawNumber,
    RawTrackerObservation,
    ZoneSnapshot,
    normalize_tracker_observation,
    resolve_place,
)
from .models import PresenceState, TrackerKind
from .storage.errors import StorageError

RAW_NUMBER_ADAPTER: Final[TypeAdapter[RawNumber]] = TypeAdapter(
    RawNumber, config=ConfigDict(strict=True)
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant, State
    from homeassistant.helpers.event import EventStateChangedData

    from .runtime.engine import RuleTransitionEngine
    from .storage.records import RuntimeStateRecord
    from .storage.resources import ConfiguredResources


@dataclass(frozen=True, slots=True)
class RuleRuntime:
    """One enabled resource graph bound to its recovered engine."""

    resources: ConfiguredResources
    engine: RuleTransitionEngine


@final
class HomeAssistantZoneLookup:
    """Read current zone geometry directly from Home Assistant state."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind the lookup to one Home Assistant state machine."""
        self._hass = hass

    def get_zone(self, entity_id: str) -> ZoneSnapshot | None:
        """Return uncached zone attributes for the current tracker sample."""
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        return ZoneSnapshot(
            latitude=_raw_number(state, ATTR_LATITUDE),
            longitude=_raw_number(state, ATTR_LONGITUDE),
            radius_m=_raw_number(state, "radius"),
        )


def normalize_ha_tracker_state(
    state: State, expected_kind: TrackerKind
) -> RawTrackerObservation | IgnoredObservation:
    """Parse one supported HA State into the raw domain boundary contract."""
    if state.domain != expected_kind.value:
        return IgnoredObservation(IgnoreReason.INVALID_STATE, state.last_updated)
    return RawTrackerObservation(
        observed_at=state.last_updated,
        latitude=_raw_number(state, ATTR_LATITUDE),
        longitude=_raw_number(state, ATTR_LONGITUDE),
        accuracy_m=_raw_number(state, ATTR_GPS_ACCURACY),
        state=state.state,
    )


def evaluate_ha_tracker_state(
    state: State,
    resources: ConfiguredResources,
    runtime_state: RuntimeStateRecord | None,
    zones: HomeAssistantZoneLookup,
    distance: HaversineDistance,
) -> EvaluatedObservation | IgnoredObservation:
    """Evaluate one current HA state with fresh place geometry."""
    boundary = normalize_ha_tracker_state(state, resources.tracker.kind)
    if isinstance(boundary, IgnoredObservation):
        return boundary
    last_at = None if runtime_state is None else runtime_state.last_processed_at
    normalized = normalize_tracker_observation(boundary, last_accepted_at=last_at)
    if isinstance(normalized, IgnoredObservation):
        return normalized
    resolved = resolve_place(resources.place, zones, observed_at=normalized.observed_at)
    if isinstance(resolved, IgnoredObservation):
        return resolved
    confirmed = (
        PresenceState.UNKNOWN if runtime_state is None else runtime_state.presence_state
    )
    return evaluate_geofence(
        normalized,
        resolved,
        confirmed,
        EvaluationThresholds(
            exit_margin_m=resources.rule.exit_margin_meters,
            max_accuracy_m=resources.rule.max_gps_accuracy_meters,
        ),
        distance=distance,
    )


@final
class GeofenceTrackerListener:
    """Own exactly one current HA listener generation for enabled trackers."""

    def __init__(
        self,
        hass: HomeAssistant,
        runtimes: tuple[RuleRuntime, ...],
        on_database_error: Callable[[], None],
    ) -> None:
        """Bind recovered engines to their configured tracker entity IDs."""
        self._hass = hass
        self._runtimes_by_entity = _index_runtimes(runtimes)
        self._on_database_error = on_database_error
        self._remove: Callable[[], None] | None = None
        self._active = False
        self._pending_states: dict[str, State] = {}
        self._processing_entities: set[str] = set()
        self._idle: anyio.Event | None = None
        self._zones = HomeAssistantZoneLookup(hass)
        self._distance = HaversineDistance()

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Return the unique enabled tracker IDs in deterministic order."""
        return tuple(sorted(self._runtimes_by_entity))

    async def async_start(self) -> None:
        """Register the current listener then synchronize existing states."""
        if self._active:
            return
        self._active = True
        self._remove = async_track_state_change_event(
            self._hass, self.entity_ids, self._async_handle_event
        )
        synchronized = False
        try:
            await self.async_sync_existing_states()
            synchronized = True
        finally:
            if not synchronized:
                await self.async_stop()

    async def async_sync_existing_states(self) -> None:
        """Process current tracker snapshots for a staged generation."""
        for entity_id in self.entity_ids:
            state = self._hass.states.get(entity_id)
            if state is not None:
                await self.async_process_state(state)

    async def async_stop(self) -> None:
        """Invalidate queued callbacks and unregister this generation."""
        self._active = False
        if self._remove is not None:
            self._remove()
            self._remove = None
        self._pending_states.clear()
        idle = self._idle
        if idle is not None:
            await idle.wait()

    async def _async_handle_event(self, event: Event[EventStateChangedData]) -> None:
        if not self._active:
            return
        state = event.data["new_state"]
        if state is None:
            return
        try:
            await self.async_process_state(state)
        except OSError, sqlite3.Error, StorageError:
            self._on_database_error()
            raise

    async def async_process_state(self, state: State) -> None:
        """Process one sample while bounding backlog to the latest per tracker."""
        entity_id = state.entity_id
        if not self._active or entity_id not in self._runtimes_by_entity:
            return
        self._pending_states[entity_id] = state
        if entity_id in self._processing_entities:
            return
        if not self._processing_entities:
            self._idle = anyio.Event()
        self._processing_entities.add(entity_id)
        try:
            while self._active:
                pending = self._pending_states.pop(entity_id, None)
                if pending is None:
                    break
                await self._async_process_state(pending)
        finally:
            _ = self._pending_states.pop(entity_id, None)
            self._processing_entities.remove(entity_id)
            if not self._processing_entities and self._idle is not None:
                self._idle.set()

    async def _async_process_state(self, state: State) -> None:
        for runtime in self._runtimes_by_entity[state.entity_id]:
            await self._async_process_runtime(runtime, state)

    async def _async_process_runtime(self, runtime: RuleRuntime, state: State) -> None:
        evaluated = evaluate_ha_tracker_state(
            state,
            runtime.resources,
            runtime.engine.current_state,
            self._zones,
            self._distance,
        )
        await runtime.engine.async_observe(evaluated)


def _raw_number(state: State, attribute: str) -> RawNumber:
    try:
        return RAW_NUMBER_ADAPTER.validate_python(state.attributes.get(attribute))
    except ValidationError:
        return None


def _index_runtimes(
    runtimes: tuple[RuleRuntime, ...],
) -> dict[str, tuple[RuleRuntime, ...]]:
    indexed: dict[str, list[RuleRuntime]] = {}
    for runtime in runtimes:
        entity_id = runtime.resources.tracker.entity_id
        indexed.setdefault(entity_id, []).append(runtime)
    return {
        entity_id: tuple(entity_runtimes)
        for entity_id, entity_runtimes in indexed.items()
    }
