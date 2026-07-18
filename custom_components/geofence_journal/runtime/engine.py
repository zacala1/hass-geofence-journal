"""Serialized persisted confirmation and cooldown state machine."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, assert_never, final

import anyio

from custom_components.geofence_journal.geofence import EvaluatedObservation
from custom_components.geofence_journal.location import IgnoredObservation
from custom_components.geofence_journal.models import PresenceState

if TYPE_CHECKING:
    from custom_components.geofence_journal.models import (
        RuleDefinition,
    )
    from custom_components.geofence_journal.storage.records import RuntimeStateRecord

    from .contracts import RuntimeDependencies, RuntimeStorage, ScheduledCall

from .confirmation import build_confirmed_transition
from .state import (
    RuntimeInvariantError,
    accepted_state_update,
    active_deadline,
    baseline_state,
    confirmation_seconds,
    confirmed_state,
    direction_cooldown,
    privacy_coordinates,
)


@final
class RuleTransitionEngine:
    """Serialize all observations and deadlines for one configured rule."""

    def __init__(
        self,
        rule: RuleDefinition,
        storage: RuntimeStorage,
        dependencies: RuntimeDependencies,
    ) -> None:
        """Bind one rule to persistence, clocks, scheduling, and event IDs."""
        self._rule = rule
        self._storage = storage
        self._dependencies = dependencies
        self._lock = anyio.Lock()
        self._state: RuntimeStateRecord | None = None
        self._scheduled: ScheduledCall | None = None
        self._accepting = True

    @property
    def current_state(self) -> RuntimeStateRecord | None:
        """Return the latest serialized in-memory state snapshot."""
        return self._state

    async def async_recover(self) -> None:
        """Load persisted state, expire cooldowns, and restore pending work."""
        async with self._lock:
            if not self._rule.enabled:
                self._accepting = False
                await self._storage.async_delete_runtime_state(str(self._rule.rule_id))
                return
            self._accepting = True
            state = await self._storage.async_runtime_state(str(self._rule.rule_id))
            if state is None:
                return
            now = self._dependencies.clock.utc_now()
            recovered = replace(
                state,
                enter_cooldown_until=active_deadline(state.enter_cooldown_until, now),
                exit_cooldown_until=active_deadline(state.exit_cooldown_until, now),
                updated_at=now,
            )
            self._state = recovered
            if recovered != state:
                await self._storage.async_save_runtime_state(recovered)
            if recovered.pending_transition is not None:
                await self._restore_pending_locked(recovered)

    async def async_observe(
        self, observation: EvaluatedObservation | IgnoredObservation
    ) -> None:
        """Apply one evaluated sample without allowing interleaving."""
        match observation:
            case IgnoredObservation():
                return
            case EvaluatedObservation() as accepted:
                async with self._lock:
                    if not self._accepting:
                        return
                    await self._accept_locked(accepted)
            case unreachable:
                assert_never(unreachable)

    async def async_deactivate(self) -> None:
        """Cancel work and transactionally remove persisted runtime state."""
        async with self._lock:
            self._accepting = False
            self._cancel_scheduled()
            self._state = None
            await self._storage.async_delete_runtime_state(str(self._rule.rule_id))

    async def async_suspend(self) -> None:
        """Cancel process-local work while preserving persisted recovery state."""
        async with self._lock:
            self._accepting = False
            self._cancel_scheduled()

    async def _accept_locked(self, observation: EvaluatedObservation) -> None:
        state = self._state
        if (
            state is not None
            and state.last_processed_at is not None
            and observation.observed_at < state.last_processed_at
        ):
            return
        if state is None:
            if observation.presence is PresenceState.UNKNOWN:
                return
            baseline = baseline_state(
                self._rule,
                observation,
                self._dependencies.clock.utc_now(),
                store_coordinates=self._dependencies.store_coordinates,
            )
            self._state = baseline
            await self._storage.async_save_runtime_state(baseline)
            return
        if observation.presence is PresenceState.UNKNOWN:
            return
        if observation.presence is state.presence_state:
            self._cancel_scheduled()
            stable = accepted_state_update(
                state,
                observation,
                self._dependencies.clock.utc_now(),
                store_coordinates=self._dependencies.store_coordinates,
                clear_pending=True,
            )
            self._state = stable
            await self._storage.async_save_runtime_state(stable)
            return
        if state.pending_transition is observation.presence:
            latest = accepted_state_update(
                state,
                observation,
                self._dependencies.clock.utc_now(),
                store_coordinates=self._dependencies.store_coordinates,
                clear_pending=False,
            )
            self._state = latest
            await self._storage.async_save_runtime_state(latest)
            deadline = latest.pending_deadline
            if deadline is not None and self._dependencies.clock.utc_now() >= deadline:
                await self._confirm_locked(latest.pending_generation)
            return
        await self._begin_pending_locked(state, observation)

    async def _begin_pending_locked(
        self, state: RuntimeStateRecord, observation: EvaluatedObservation
    ) -> None:
        self._cancel_scheduled()
        now = self._dependencies.clock.utc_now()
        seconds = confirmation_seconds(self._rule, observation.presence)
        pending = replace(
            state,
            pending_transition=observation.presence,
            pending_started_at=now,
            pending_deadline=now + timedelta(seconds=seconds),
            pending_generation=state.pending_generation + 1,
            latest_observation_at=observation.observed_at,
            latest_coordinates=privacy_coordinates(
                observation,
                store_coordinates=self._dependencies.store_coordinates,
            ),
            latest_accuracy_m=observation.accuracy_m,
            last_processed_at=observation.observed_at,
            updated_at=now,
        )
        self._state = pending
        await self._storage.async_save_runtime_state(pending)
        await self._restore_pending_locked(pending)

    async def _restore_pending_locked(self, state: RuntimeStateRecord) -> None:
        deadline = state.pending_deadline
        if deadline is None:
            return
        delay = max(
            0.0, (deadline - self._dependencies.clock.utc_now()).total_seconds()
        )
        if delay == 0.0:
            await self._confirm_locked(state.pending_generation)
            return
        generation = state.pending_generation
        self._scheduled = self._dependencies.scheduler.schedule(
            delay, lambda: self._deadline_callback(generation)
        )

    async def _deadline_callback(self, generation: int) -> None:
        async with self._lock:
            if not self._accepting:
                return
            await self._confirm_locked(generation)

    async def _confirm_locked(self, generation: int) -> None:
        state = self._state
        if state is None or state.pending_generation != generation:
            return
        target = state.pending_transition
        deadline = state.pending_deadline
        started = state.pending_started_at
        if target is None or deadline is None or started is None:
            return
        now = self._dependencies.clock.utc_now()
        if now < deadline:
            await self._restore_pending_locked(state)
            return
        reevaluated = await self._dependencies.confirmation_evaluator.async_evaluate(
            state
        )
        if reevaluated is None:
            self._scheduled = None
            return
        if reevaluated is not target:
            cancelled = replace(
                state,
                pending_transition=None,
                pending_started_at=None,
                pending_deadline=None,
                updated_at=now,
            )
            self._state = cancelled
            await self._storage.async_save_runtime_state(cancelled)
            self._scheduled = None
            return
        cooldown = direction_cooldown(state, target)
        if cooldown is not None and now < cooldown:
            corrected = confirmed_state(state, target, now, None, self._rule)
            self._state = corrected
            await self._storage.async_save_runtime_state(corrected)
            return
        confirmed, transition = build_confirmed_transition(
            self._rule,
            self._dependencies,
            state,
            now,
            generation,
        )
        result = await self._storage.async_confirm_transition(transition)
        if result.created:
            self._state = confirmed
            await self._dependencies.observer.on_transition(result, confirmed)
        else:
            persisted = await self._storage.async_runtime_state(str(self._rule.rule_id))
            if persisted is None:
                raise RuntimeInvariantError(
                    detail="duplicate event has no committed runtime state"
                )
            self._state = persisted
        self._scheduled = None

    def _cancel_scheduled(self) -> None:
        if self._scheduled is not None:
            self._scheduled.cancel()
            self._scheduled = None
