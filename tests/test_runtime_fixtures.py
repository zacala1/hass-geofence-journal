"""Typed factories shared only by runtime-focused tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, final

from custom_components.geofence_journal.geofence import EvaluatedObservation
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    JournalDefinition,
    JournalId,
    LocationSource,
    Meters,
    PlaceId,
    PresenceState,
    RuleDefinition,
    RuleId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.runtime.contracts import RuntimeDependencies
from custom_components.geofence_journal.runtime.engine import RuleTransitionEngine
from custom_components.geofence_journal.storage.async_adapter import AsyncSQLiteStore
from custom_components.geofence_journal.storage.resources import ConfiguredResources

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from custom_components.geofence_journal.runtime.contracts import TransitionObserver
    from custom_components.geofence_journal.storage.repository import SQLiteStore

RUNTIME_START = datetime(2026, 7, 18, 12, tzinfo=UTC)


def runtime_resources(
    *,
    enter_seconds: int = 120,
    exit_seconds: int = 180,
    cooldown_seconds: int = 300,
    enabled: bool = True,
) -> ConfiguredResources:
    """Build one valid linked rule graph for runtime tests."""
    return ConfiguredResources(
        tracker=TrackerDefinition(
            tracker_id=TrackerId("tracker-1"),
            entity_id="person.fixture",
            kind=TrackerKind.PERSON,
            name="Fixture",
            enabled=True,
        ),
        place=CoordinatePlace(
            place_id=PlaceId("place-1"),
            name="Fixture",
            center=Coordinates(latitude=0, longitude=0),
            radius_m=Meters(100),
        ),
        journal=JournalDefinition(
            journal_id=JournalId("journal-1"), name="Fixture", enabled=True
        ),
        rule=RuleDefinition(
            rule_id=RuleId("rule-1"),
            tracker_id=TrackerId("tracker-1"),
            place_id=PlaceId("place-1"),
            journal_id=JournalId("journal-1"),
            enabled=enabled,
            enter_confirmation_seconds=Seconds(enter_seconds),
            exit_confirmation_seconds=Seconds(exit_seconds),
            cooldown_seconds=Seconds(cooldown_seconds),
            exit_margin_meters=Meters(50),
            max_gps_accuracy_meters=Meters(100),
        ),
    )


def seed_runtime_resources(
    store: SQLiteStore, resources: ConfiguredResources | None = None
) -> None:
    """Persist a complete valid resource graph."""
    selected = runtime_resources() if resources is None else resources
    store.upsert_tracker(selected.tracker, RUNTIME_START)
    store.upsert_place(selected.place, RUNTIME_START)
    store.upsert_journal(selected.journal, RUNTIME_START)
    store.upsert_rule(selected.rule, RUNTIME_START)


@final
class RecoveryClock:
    """Mutable deterministic wall and monotonic clock."""

    def __init__(self, wall: datetime) -> None:
        self.wall = wall
        self.ticks = 0.0

    def utc_now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.ticks


@final
class RecoveryCall:
    """Cancellable callback retained for explicit stale replay."""

    def __init__(self, due: float, callback: Callable[[], Awaitable[None]]) -> None:
        self.due = due
        self.callback = callback
        self.active = True

    def cancel(self) -> None:
        self.active = False


@final
class RecoveryScheduler:
    """Execute scheduled work only when deterministic time advances."""

    def __init__(self, clock: RecoveryClock) -> None:
        self.clock = clock
        self.calls: list[RecoveryCall] = []

    def schedule(
        self, delay_seconds: float, callback: Callable[[], Awaitable[None]]
    ) -> RecoveryCall:
        call = RecoveryCall(self.clock.ticks + delay_seconds, callback)
        self.calls.append(call)
        return call

    async def advance(self, seconds: float) -> None:
        self.clock.ticks += seconds
        self.clock.wall += timedelta(seconds=seconds)
        due = [
            call for call in self.calls if call.active and call.due <= self.clock.ticks
        ]
        for call in due:
            call.active = False
            await call.callback()


@final
class RecoveryEventIds:
    """Generate deterministic event identifiers."""

    def __init__(self) -> None:
        self.count = 0

    def next_id(self) -> str:
        self.count += 1
        return f"recovery-event-{self.count}"


def recovery_observation(state: PresenceState, at: datetime) -> EvaluatedObservation:
    """Build one accepted deterministic observation."""
    return EvaluatedObservation(
        presence=state,
        distance_m=Meters(10),
        observed_at=at,
        coordinates=Coordinates(37.0, 127.0),
        accuracy_m=Meters(5),
    )


async def open_runtime_engine(
    path: Path,
    rule: RuleDefinition,
    now: datetime,
    *,
    store_coordinates: bool = False,
    observer: TransitionObserver | None = None,
) -> tuple[RuleTransitionEngine, AsyncSQLiteStore, RecoveryScheduler]:
    """Open and recover one engine against a real SQLite file."""
    store = AsyncSQLiteStore(path)
    await store.async_open()
    clock = RecoveryClock(now)
    scheduler = RecoveryScheduler(clock)
    dependencies = RuntimeDependencies(
        clock=clock,
        scheduler=scheduler,
        event_ids=RecoveryEventIds(),
        source=LocationSource.GPS,
        store_coordinates=store_coordinates,
    )
    if observer is not None:
        dependencies = RuntimeDependencies(
            clock=clock,
            scheduler=scheduler,
            event_ids=RecoveryEventIds(),
            source=LocationSource.GPS,
            store_coordinates=store_coordinates,
            observer=observer,
        )
    engine = RuleTransitionEngine(rule, store, dependencies)
    await engine.async_recover()
    return engine, store, scheduler
