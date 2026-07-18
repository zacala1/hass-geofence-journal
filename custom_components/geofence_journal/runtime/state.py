"""Pure runtime-state construction and direction mappings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, assert_never, override

from custom_components.geofence_journal.models import (
    LocationEventType,
    PresenceState,
)
from custom_components.geofence_journal.storage.records import RuntimeStateRecord

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.geofence_journal.geofence import EvaluatedObservation
    from custom_components.geofence_journal.models import Coordinates, RuleDefinition


@dataclass(frozen=True, slots=True)
class RuntimeInvariantError(Exception):
    """Persisted runtime state violates an engine invariant."""

    detail: str

    @override
    def __str__(self) -> str:
        """Return the violated invariant detail."""
        return self.detail


def baseline_state(
    rule: RuleDefinition,
    observation: EvaluatedObservation,
    now: datetime,
    *,
    store_coordinates: bool,
) -> RuntimeStateRecord:
    """Create a no-event baseline from the first classifiable sample."""
    return RuntimeStateRecord(
        rule_id=str(rule.rule_id),
        presence_state=observation.presence,
        last_event_id=None,
        last_event_type=None,
        last_event_at=None,
        enter_cooldown_until=None,
        exit_cooldown_until=None,
        pending_transition=None,
        pending_started_at=None,
        pending_deadline=None,
        pending_generation=0,
        latest_observation_at=observation.observed_at,
        latest_coordinates=(observation.coordinates if store_coordinates else None),
        latest_accuracy_m=observation.accuracy_m,
        last_processed_at=observation.observed_at,
        updated_at=now,
    )


def accepted_state_update(
    state: RuntimeStateRecord,
    observation: EvaluatedObservation,
    now: datetime,
    *,
    store_coordinates: bool,
    clear_pending: bool,
) -> RuntimeStateRecord:
    """Persist the latest accepted sample and optionally cancel pending work."""
    updated = replace(
        state,
        latest_observation_at=observation.observed_at,
        latest_coordinates=privacy_coordinates(
            observation, store_coordinates=store_coordinates
        ),
        latest_accuracy_m=observation.accuracy_m,
        last_processed_at=observation.observed_at,
        updated_at=now,
    )
    if not clear_pending:
        return updated
    return replace(
        updated,
        pending_transition=None,
        pending_started_at=None,
        pending_deadline=None,
    )


def privacy_coordinates(
    observation: EvaluatedObservation, *, store_coordinates: bool
) -> Coordinates | None:
    """Omit coordinates only when privacy storage is disabled."""
    return observation.coordinates if store_coordinates else None


def confirmation_seconds(rule: RuleDefinition, target: PresenceState) -> int:
    """Return the independently configured confirmation interval."""
    match target:
        case PresenceState.INSIDE:
            return int(rule.enter_confirmation_seconds)
        case PresenceState.OUTSIDE:
            return int(rule.exit_confirmation_seconds)
        case PresenceState.UNKNOWN:
            return 0
        case unreachable:
            assert_never(unreachable)


def event_type_for(target: PresenceState) -> LocationEventType:
    """Map a confirmed stable target to its event direction."""
    match target:
        case PresenceState.INSIDE:
            return LocationEventType.ENTER
        case PresenceState.OUTSIDE:
            return LocationEventType.EXIT
        case PresenceState.UNKNOWN:
            raise RuntimeInvariantError(detail="unknown cannot be confirmed")
        case unreachable:
            assert_never(unreachable)


def direction_cooldown(
    state: RuntimeStateRecord, target: PresenceState
) -> datetime | None:
    """Read the cooldown belonging only to the proposed direction."""
    match target:
        case PresenceState.INSIDE:
            return state.enter_cooldown_until
        case PresenceState.OUTSIDE:
            return state.exit_cooldown_until
        case PresenceState.UNKNOWN:
            return None
        case unreachable:
            assert_never(unreachable)


def confirmed_state(
    state: RuntimeStateRecord,
    target: PresenceState,
    now: datetime,
    event_id: str | None,
    rule: RuleDefinition,
) -> RuntimeStateRecord:
    """Clear pending state and apply stable correction plus optional event data."""
    event_type = None if event_id is None else event_type_for(target)
    cooldown = now + timedelta(seconds=int(rule.cooldown_seconds))
    enter_cooldown = state.enter_cooldown_until
    exit_cooldown = state.exit_cooldown_until
    if event_id is not None:
        match target:
            case PresenceState.INSIDE:
                enter_cooldown = cooldown
            case PresenceState.OUTSIDE:
                exit_cooldown = cooldown
            case PresenceState.UNKNOWN:
                raise RuntimeInvariantError(detail="unknown cannot be confirmed")
            case unreachable:
                assert_never(unreachable)
    return replace(
        state,
        presence_state=target,
        last_event_id=state.last_event_id if event_id is None else event_id,
        last_event_type=state.last_event_type if event_type is None else event_type,
        last_event_at=state.last_event_at if event_id is None else now,
        enter_cooldown_until=enter_cooldown,
        exit_cooldown_until=exit_cooldown,
        pending_transition=None,
        pending_started_at=None,
        pending_deadline=None,
        updated_at=now,
    )


def active_deadline(deadline: datetime | None, now: datetime) -> datetime | None:
    """Discard a cooldown at its exact UTC boundary or later."""
    return deadline if deadline is not None and deadline > now else None
