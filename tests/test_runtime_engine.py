from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, final

import pytest
from custom_components.geofence_journal.geofence import EvaluatedObservation
from custom_components.geofence_journal.location import IgnoredObservation, IgnoreReason
from custom_components.geofence_journal.models import (
    Coordinates,
    LocationSource,
    Meters,
    PresenceState,
)
from custom_components.geofence_journal.runtime.contracts import RuntimeDependencies
from custom_components.geofence_journal.runtime.engine import RuleTransitionEngine
from custom_components.geofence_journal.storage.async_adapter import AsyncSQLiteStore
from custom_components.geofence_journal.storage.repository import SQLiteStore
from tests.test_runtime_fixtures import runtime_resources

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from custom_components.geofence_journal.storage.records import (
        RuntimeStateRecord,
        TransitionResult,
    )

START = datetime(2026, 7, 18, 12, tzinfo=UTC)


@final
class FakeClock:
    """Mutable deterministic wall and monotonic clock for runtime tests."""

    def __init__(self, wall: datetime = START) -> None:
        self.wall = wall
        self.ticks = 0.0

    def utc_now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.ticks


@final
class FakeCall:
    """Cancellable scheduled callback."""

    def __init__(self, due: float, callback: Callable[[], Awaitable[None]]) -> None:
        self.due = due
        self.callback = callback
        self.active = True

    def cancel(self) -> None:
        self.active = False


@final
class FakeScheduler:
    """Run callbacks only when explicitly advanced by a test."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[FakeCall] = []

    def schedule(
        self, delay_seconds: float, callback: Callable[[], Awaitable[None]]
    ) -> FakeCall:
        call = FakeCall(self.clock.ticks + delay_seconds, callback)
        self.calls.append(call)
        return call

    async def advance(
        self, seconds: float, *, wall_seconds: float | None = None
    ) -> None:
        self.clock.ticks += seconds
        self.clock.wall += timedelta(
            seconds=seconds if wall_seconds is None else wall_seconds
        )
        due = [
            call for call in self.calls if call.active and call.due <= self.clock.ticks
        ]
        for call in due:
            call.active = False
            await call.callback()


@final
class EventIds:
    """Return deterministic event identifiers."""

    def __init__(self) -> None:
        self.count = 0

    def next_id(self) -> str:
        self.count += 1
        return f"event-{self.count}"


@final
class RecordingObserver:
    """Record newly committed transition notifications."""

    def __init__(self) -> None:
        self.calls: list[tuple[TransitionResult, RuntimeStateRecord]] = []

    async def on_transition(
        self, result: TransitionResult, state: RuntimeStateRecord
    ) -> None:
        self.calls.append((result, state))


def accepted(state: PresenceState, at: datetime) -> EvaluatedObservation:
    return EvaluatedObservation(
        presence=state,
        distance_m=Meters(10),
        observed_at=at,
        coordinates=Coordinates(37.0, 127.0),
        accuracy_m=Meters(5),
    )


async def opened_engine(
    path: Path, *, enter_seconds: int = 120
) -> tuple[RuleTransitionEngine, AsyncSQLiteStore, FakeClock, FakeScheduler]:
    store = AsyncSQLiteStore(path)
    await store.async_open()
    resources = runtime_resources(enter_seconds=enter_seconds)
    await store.async_upsert_resources(resources, START)
    clock = FakeClock()
    scheduler = FakeScheduler(clock)
    engine = RuleTransitionEngine(
        resources.rule,
        store,
        RuntimeDependencies(
            clock=clock,
            scheduler=scheduler,
            event_ids=EventIds(),
            source=LocationSource.GPS,
        ),
    )
    await engine.async_recover()
    return engine, store, clock, scheduler


@pytest.mark.asyncio
async def test_first_valid_observation_establishes_baseline_without_event(
    tmp_path: Path,
) -> None:
    # Given
    engine, store, _clock, _scheduler = await opened_engine(tmp_path / "baseline.db")

    # When
    await engine.async_observe(accepted(PresenceState.OUTSIDE, START))

    # Then
    await store.async_close()
    with SQLiteStore(tmp_path / "baseline.db") as reopened:
        state = reopened.runtime_state("rule-1")
        assert state is not None
        assert state.presence_state is PresenceState.OUTSIDE
        assert reopened.event_count() == 0


@pytest.mark.asyncio
async def test_contradictory_valid_sample_cancels_but_ignored_sample_does_not(
    tmp_path: Path,
) -> None:
    # Given
    engine, store, clock, scheduler = await opened_engine(tmp_path / "cancel.db")
    await engine.async_observe(accepted(PresenceState.OUTSIDE, START))
    await engine.async_observe(
        accepted(PresenceState.INSIDE, START + timedelta(seconds=1))
    )

    # When ignored, then contradictory accepted
    await engine.async_observe(
        IgnoredObservation(
            IgnoreReason.EXCESSIVE_ACCURACY, START + timedelta(seconds=2)
        )
    )
    pending = await store.async_runtime_state("rule-1")
    await engine.async_observe(
        accepted(PresenceState.OUTSIDE, START + timedelta(seconds=3))
    )
    await scheduler.advance(120)

    # Then
    cancelled = await store.async_runtime_state("rule-1")
    assert pending is not None
    assert pending.pending_transition is PresenceState.INSIDE
    assert cancelled is not None
    assert cancelled.pending_transition is None
    await store.async_close()
    assert clock.wall == START + timedelta(seconds=120)


@pytest.mark.asyncio
async def test_exact_deadline_confirms_once_and_stale_callback_is_noop(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "deadline.db"
    engine, store, _clock, scheduler = await opened_engine(path)
    await engine.async_observe(accepted(PresenceState.OUTSIDE, START))
    await engine.async_observe(
        accepted(PresenceState.INSIDE, START + timedelta(seconds=1))
    )

    # When
    stale_callback = scheduler.calls[0].callback
    await scheduler.advance(120)
    await stale_callback()

    # Then
    state = await store.async_runtime_state("rule-1")
    assert state is not None
    assert state.presence_state is PresenceState.INSIDE
    await store.async_close()
    with SQLiteStore(path) as reopened:
        assert reopened.event_count() == 1


@pytest.mark.asyncio
async def test_losing_duplicate_confirmation_reloads_committed_runtime_state(
    tmp_path: Path,
) -> None:
    # Given two recovered processes holding the same pending generation
    path = tmp_path / "duplicate-callback.db"
    winner, winner_store, _clock, winner_scheduler = await opened_engine(path)
    await winner.async_observe(accepted(PresenceState.OUTSIDE, START))
    await winner.async_observe(
        accepted(PresenceState.INSIDE, START + timedelta(seconds=1))
    )
    loser_store = AsyncSQLiteStore(path)
    await loser_store.async_open()
    loser_clock = FakeClock(START + timedelta(seconds=10))
    loser_scheduler = FakeScheduler(loser_clock)
    loser_observer = RecordingObserver()
    resources = runtime_resources()
    loser = RuleTransitionEngine(
        resources.rule,
        loser_store,
        RuntimeDependencies(
            clock=loser_clock,
            scheduler=loser_scheduler,
            event_ids=EventIds(),
            source=LocationSource.GPS,
            observer=loser_observer,
        ),
    )
    await loser.async_recover()

    # When the winner commits on time and the loser replays late
    await winner_scheduler.advance(120)
    await loser_scheduler.advance(120)

    # Then loser memory matches the already committed database row
    persisted = await loser_store.async_runtime_state("rule-1")
    assert loser.current_state == persisted
    assert loser_observer.calls == []
    await winner_store.async_close()
    await loser_store.async_close()
    with SQLiteStore(path) as reopened:
        assert reopened.event_count() == 1


@pytest.mark.asyncio
async def test_wall_clock_rollback_cannot_confirm_before_utc_deadline(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "rollback-clock.db"
    engine, store, _clock, scheduler = await opened_engine(path)
    await engine.async_observe(accepted(PresenceState.OUTSIDE, START))
    await engine.async_observe(
        accepted(PresenceState.INSIDE, START + timedelta(seconds=1))
    )

    # When monotonic reaches the timer while wall time rolls backward
    await scheduler.advance(120, wall_seconds=-30)

    # Then no early event exists; reaching the persisted UTC deadline confirms
    with SQLiteStore(path) as concurrent:
        assert concurrent.event_count() == 0
    await scheduler.advance(150)
    await store.async_close()
    with SQLiteStore(path) as reopened:
        assert reopened.event_count() == 1
