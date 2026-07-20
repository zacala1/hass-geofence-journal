from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, final

import pytest
from custom_components.geofence_journal.models import (
    LocationSource,
    PresenceState,
)
from custom_components.geofence_journal.runtime.confirmation import (
    build_confirmed_transition,
)
from custom_components.geofence_journal.runtime.contracts import RuntimeDependencies
from custom_components.geofence_journal.runtime.engine import RuleTransitionEngine
from custom_components.geofence_journal.runtime.state import RuntimeInvariantError
from custom_components.geofence_journal.storage.records import (
    ConfirmedTransition,
    RuntimeStateRecord,
    TransitionResult,
)
from custom_components.geofence_journal.storage.repository import SQLiteStore
from custom_components.geofence_journal.storage.transitions import event_count
from tests.test_runtime_fixtures import (
    RecoveryClock,
    RecoveryEventIds,
    RecoveryScheduler,
    open_runtime_engine,
    recovery_observation,
    runtime_resources,
    seed_runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def runtime_state(
    *,
    pending_transition: PresenceState | None = None,
    pending_started_at: datetime | None = None,
    pending_deadline: datetime | None = None,
    pending_generation: int = 0,
) -> RuntimeStateRecord:
    return RuntimeStateRecord(
        rule_id="rule-1",
        presence_state=PresenceState.OUTSIDE,
        last_event_id=None,
        last_event_type=None,
        last_event_at=None,
        enter_cooldown_until=None,
        exit_cooldown_until=None,
        pending_transition=pending_transition,
        pending_started_at=pending_started_at,
        pending_deadline=pending_deadline,
        pending_generation=pending_generation,
        latest_observation_at=NOW,
        latest_coordinates=None,
        latest_accuracy_m=None,
        last_processed_at=NOW,
        updated_at=NOW,
    )


@final
class QueuedStorage:
    def __init__(self, states: list[RuntimeStateRecord | None]) -> None:
        self.states = states
        self.saved: list[RuntimeStateRecord] = []
        self.deleted: list[str] = []

    async def async_runtime_state(self, rule_id: str) -> RuntimeStateRecord | None:
        _ = rule_id
        return self.states.pop(0)

    async def async_save_runtime_state(self, state: RuntimeStateRecord) -> None:
        self.saved.append(state)

    async def async_delete_runtime_state(self, rule_id: str) -> None:
        self.deleted.append(rule_id)

    async def async_confirm_transition(
        self, transition: ConfirmedTransition
    ) -> TransitionResult:
        return TransitionResult(event_id=transition.event_id, created=False)


@final
class MissingConfirmation:
    async def async_evaluate(self, state: RuntimeStateRecord) -> PresenceState | None:
        _ = state
        return None


def dependencies(
    clock: RecoveryClock,
    *,
    missing_confirmation: bool = False,
) -> RuntimeDependencies:
    evaluator = MissingConfirmation() if missing_confirmation else None
    if evaluator is None:
        return RuntimeDependencies(
            clock=clock,
            scheduler=RecoveryScheduler(clock),
            event_ids=RecoveryEventIds(),
            source=LocationSource.GPS,
        )
    return RuntimeDependencies(
        clock=clock,
        scheduler=RecoveryScheduler(clock),
        event_ids=RecoveryEventIds(),
        source=LocationSource.GPS,
        confirmation_evaluator=evaluator,
    )


async def test_disabled_rule_recovery_deletes_stale_runtime_state() -> None:
    storage = QueuedStorage([runtime_state()])
    engine = RuleTransitionEngine(
        runtime_resources(enabled=False).rule,
        storage,
        dependencies(RecoveryClock(NOW)),
    )

    await engine.async_recover()

    assert storage.deleted == ["rule-1"]
    assert engine.current_state is None


async def test_unknown_samples_and_suspended_engine_do_not_create_baseline() -> None:
    storage = QueuedStorage([None])
    engine = RuleTransitionEngine(
        runtime_resources().rule,
        storage,
        dependencies(RecoveryClock(NOW)),
    )
    await engine.async_recover()

    await engine.async_observe(recovery_observation(PresenceState.UNKNOWN, NOW))
    await engine.async_suspend()
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, NOW))

    assert engine.current_state is None
    assert storage.saved == []


async def test_unknown_sample_does_not_replace_existing_baseline() -> None:
    state = runtime_state()
    storage = QueuedStorage([state])
    engine = RuleTransitionEngine(
        runtime_resources().rule,
        storage,
        dependencies(RecoveryClock(NOW)),
    )
    await engine.async_recover()

    await engine.async_observe(
        recovery_observation(PresenceState.UNKNOWN, NOW + timedelta(seconds=1))
    )

    assert engine.current_state == state
    assert storage.saved == []


async def test_recovery_tolerates_pending_state_without_deadline() -> None:
    state = runtime_state(pending_transition=PresenceState.INSIDE)
    storage = QueuedStorage([state])
    engine = RuleTransitionEngine(
        runtime_resources().rule,
        storage,
        dependencies(RecoveryClock(NOW)),
    )

    await engine.async_recover()

    assert engine.current_state == state


async def test_suspended_deadline_callback_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "suspended-callback.db"
    resources = runtime_resources(enter_seconds=30)
    with SQLiteStore(path) as store:
        seed_runtime_resources(store, resources)
    engine, store, scheduler = await open_runtime_engine(path, resources.rule, NOW)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, NOW))
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, NOW + timedelta(seconds=1))
    )
    callback = scheduler.calls[0].callback

    await engine.async_suspend()
    await callback()

    assert await store.async_run_operation(event_count) == 0
    await store.async_close()


async def test_unusable_confirmation_leaves_pending_state_for_future_input() -> None:
    pending = runtime_state(
        pending_transition=PresenceState.INSIDE,
        pending_started_at=NOW - timedelta(seconds=30),
        pending_deadline=NOW,
        pending_generation=1,
    )
    storage = QueuedStorage([pending])
    engine = RuleTransitionEngine(
        runtime_resources().rule,
        storage,
        dependencies(RecoveryClock(NOW), missing_confirmation=True),
    )

    await engine.async_recover()

    assert engine.current_state == pending


async def test_duplicate_without_committed_runtime_state_is_rejected() -> None:
    pending = runtime_state(
        pending_transition=PresenceState.INSIDE,
        pending_started_at=NOW - timedelta(seconds=30),
        pending_deadline=NOW,
        pending_generation=1,
    )
    storage = QueuedStorage([pending, None])
    engine = RuleTransitionEngine(
        runtime_resources().rule,
        storage,
        dependencies(RecoveryClock(NOW)),
    )

    with pytest.raises(
        RuntimeInvariantError, match="duplicate event has no committed runtime state"
    ):
        await engine.async_recover()


def test_confirmed_transition_requires_complete_pending_state() -> None:
    clock = RecoveryClock(NOW)

    with pytest.raises(RuntimeInvariantError, match="pending transition is incomplete"):
        _ = build_confirmed_transition(
            runtime_resources().rule,
            dependencies(clock),
            runtime_state(pending_transition=PresenceState.INSIDE),
            NOW,
            1,
        )
