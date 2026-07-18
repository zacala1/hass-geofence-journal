"""Construction of one atomically persisted confirmed transition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.geofence_journal.storage.records import ConfirmedTransition

from .state import RuntimeInvariantError, confirmed_state, event_type_for

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.geofence_journal.models import RuleDefinition
    from custom_components.geofence_journal.storage.records import RuntimeStateRecord

    from .contracts import RuntimeDependencies


def build_confirmed_transition(
    rule: RuleDefinition,
    dependencies: RuntimeDependencies,
    state: RuntimeStateRecord,
    now: datetime,
    generation: int,
) -> tuple[RuntimeStateRecord, ConfirmedTransition]:
    """Build matching runtime and event records for one confirmation commit."""
    target = state.pending_transition
    started = state.pending_started_at
    deadline = state.pending_deadline
    if target is None or started is None or deadline is None:
        raise RuntimeInvariantError(detail="pending transition is incomplete")
    event_id = dependencies.event_ids.next_id()
    confirmed = confirmed_state(state, target, now, event_id, rule)
    transition = ConfirmedTransition(
        event_id=event_id,
        rule_id=str(rule.rule_id),
        tracker_id=str(rule.tracker_id),
        place_id=str(rule.place_id),
        journal_id=str(rule.journal_id),
        event_type=event_type_for(target),
        source=dependencies.source,
        target_state=target,
        occurred_at=started,
        confirmed_at=now,
        generation=generation,
        confirmed_deadline=deadline,
        coordinates=state.latest_coordinates,
        accuracy_m=state.latest_accuracy_m,
        runtime_state=confirmed,
    )
    return confirmed, transition
