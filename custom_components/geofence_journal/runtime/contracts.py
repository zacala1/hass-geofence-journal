"""Narrow capabilities injected into the deterministic runtime engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, final

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from custom_components.geofence_journal.models import Clock, LocationSource
    from custom_components.geofence_journal.storage.records import (
        ConfirmedTransition,
        RuntimeStateRecord,
        TransitionResult,
    )


class ScheduledCall(Protocol):
    """One cancellable in-process deadline callback."""

    def cancel(self) -> None:
        """Prevent the callback if it has not started."""
        ...


class Scheduler(Protocol):
    """Process-local monotonic scheduling capability."""

    def schedule(
        self, delay_seconds: float, callback: Callable[[], Awaitable[None]]
    ) -> ScheduledCall:
        """Schedule an awaitable callback after a monotonic delay."""
        ...


class EventIdFactory(Protocol):
    """Generate a unique persisted event identifier."""

    def next_id(self) -> str:
        """Return the next identifier."""
        ...


class TransitionObserver(Protocol):
    """Observe only newly committed persisted transitions."""

    async def on_transition(
        self, result: TransitionResult, state: RuntimeStateRecord
    ) -> None:
        """Handle one created transition after its atomic commit."""
        ...


@final
class NoopTransitionObserver:
    """Default observer for runtimes without outward notifications."""

    async def on_transition(
        self, result: TransitionResult, state: RuntimeStateRecord
    ) -> None:
        """Accept a committed transition without side effects."""
        _ = (result, state)


class RuntimeStorage(Protocol):
    """Persistence surface required by one transition engine."""

    async def async_runtime_state(self, rule_id: str) -> RuntimeStateRecord | None:
        """Load one runtime row."""
        ...

    async def async_save_runtime_state(self, state: RuntimeStateRecord) -> None:
        """Persist one runtime row."""
        ...

    async def async_delete_runtime_state(self, rule_id: str) -> None:
        """Delete one runtime row."""
        ...

    async def async_confirm_transition(
        self, transition: ConfirmedTransition
    ) -> TransitionResult:
        """Atomically append an event and replace its runtime row."""
        ...


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    """Injected deterministic capabilities used by one engine."""

    clock: Clock
    scheduler: Scheduler
    event_ids: EventIdFactory
    source: LocationSource
    store_coordinates: bool = False
    observer: TransitionObserver = field(default_factory=NoopTransitionObserver)
