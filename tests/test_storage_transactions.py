from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from threading import Barrier
from typing import TYPE_CHECKING

import anyio
import pytest

if TYPE_CHECKING:
    from pathlib import Path
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    JournalDefinition,
    JournalId,
    LocationEventType,
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
from custom_components.geofence_journal.storage import (
    AsyncSQLiteStore,
    ConfiguredResources,
    ConfirmedTransition,
    InjectedStorageFaultError,
    SQLiteStore,
)
from custom_components.geofence_journal.storage.transitions import (
    confirm_transition as persist_transition,
)

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def _resources() -> ConfiguredResources:
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
            enabled=True,
            enter_confirmation_seconds=Seconds(120),
            exit_confirmation_seconds=Seconds(180),
            cooldown_seconds=Seconds(300),
            exit_margin_meters=Meters(50),
            max_gps_accuracy_meters=Meters(100),
        ),
    )


def _seed(store: SQLiteStore) -> None:
    resources = _resources()
    store.upsert_tracker(resources.tracker, NOW)
    store.upsert_place(resources.place, NOW)
    store.upsert_journal(resources.journal, NOW)
    store.upsert_rule(resources.rule, NOW)


def _transition(event_id: str = "event-1") -> ConfirmedTransition:
    return ConfirmedTransition(
        event_id=event_id,
        rule_id="rule-1",
        tracker_id="tracker-1",
        place_id="place-1",
        journal_id="journal-1",
        event_type=LocationEventType.ENTER,
        source=LocationSource.GPS,
        target_state=PresenceState.INSIDE,
        occurred_at=NOW,
        confirmed_at=NOW,
        generation=7,
        confirmed_deadline=NOW,
    )


def test_event_and_runtime_commit_survive_reopen(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "commit.db"
    with SQLiteStore(database_path) as store:
        _seed(store)

        # When
        result = store.confirm_transition(_transition())

    # Then
    with SQLiteStore(database_path) as reopened:
        assert result.created is True
        assert reopened.event_count() == 1
        runtime = reopened.runtime_state("rule-1")
        assert runtime is not None
        assert runtime.presence_state is PresenceState.INSIDE


def test_fault_after_event_insert_rolls_back_both_rows(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "rollback.db"
    with SQLiteStore(database_path) as store:
        _seed(store)

        # When
        with pytest.raises(InjectedStorageFaultError):
            _ = store.confirm_transition(_transition(), fail_after_event_insert=True)

    # Then
    with SQLiteStore(database_path) as reopened:
        assert reopened.event_count() == 0
        assert reopened.runtime_state("rule-1") is None


def test_duplicate_transition_returns_existing_event(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "duplicate.db"
    with SQLiteStore(database_path) as store:
        _seed(store)
        first = store.confirm_transition(_transition())

        # When
        duplicate = store.confirm_transition(
            ConfirmedTransition(
                event_id="event-replayed",
                rule_id="rule-1",
                tracker_id="tracker-1",
                place_id="place-1",
                journal_id="journal-1",
                event_type=LocationEventType.ENTER,
                source=LocationSource.GPS,
                target_state=PresenceState.INSIDE,
                occurred_at=NOW,
                confirmed_at=NOW,
                generation=7,
                confirmed_deadline=NOW,
            )
        )

        # Then
        assert first.created is True
        assert duplicate.created is False
        assert duplicate.event_id == "event-1"
        assert store.event_count() == 1


def test_concurrent_duplicate_transition_returns_the_committed_event(
    tmp_path: Path,
) -> None:
    # Given: two independent WAL connections start the same write concurrently.
    database_path = tmp_path / "concurrent-duplicate.db"
    with SQLiteStore(database_path) as store:
        _seed(store)
    begin_barrier = Barrier(2)

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(
            database_path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        _ = connection.execute("PRAGMA foreign_keys=ON")
        _ = connection.execute("PRAGMA journal_mode=WAL")
        _ = connection.execute("PRAGMA busy_timeout=5000")

        def synchronize_begin(statement: str) -> None:
            if statement == "BEGIN IMMEDIATE":
                _ = begin_barrier.wait(timeout=5)

        connection.set_trace_callback(synchronize_begin)
        return connection

    with (
        closing(connect()) as first_connection,
        closing(connect()) as second_connection,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        # When: both connections confirm one durable transition identity.
        first_future = executor.submit(
            persist_transition, first_connection, _transition("event-first")
        )
        second_future = executor.submit(
            persist_transition, second_connection, _transition("event-second")
        )
        results = (first_future.result(), second_future.result())

    # Then: one writer creates the event and the other observes that same commit.
    assert {result.created for result in results} == {False, True}
    assert len({result.event_id for result in results}) == 1
    with SQLiteStore(database_path) as reopened:
        assert reopened.event_count() == 1
        runtime = reopened.runtime_state("rule-1")
        assert runtime is not None
        assert runtime.last_event_id == results[0].event_id


@pytest.mark.asyncio
async def test_async_adapter_offloads_and_drains_before_close(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "async.db"
    store = AsyncSQLiteStore(database_path)
    await store.async_open()
    await store.async_upsert_resources(_resources(), NOW)

    # When
    async with anyio.create_task_group() as task_group:
        _ = task_group.start_soon(store.async_confirm_transition, _transition())
        _ = task_group.start_soon(store.async_close)

    # Then
    with SQLiteStore(database_path) as reopened:
        assert reopened.event_count() == 1


def test_locked_database_respects_bounded_timeout(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "locked.db"
    with SQLiteStore(database_path, busy_timeout_ms=50) as store:
        _seed(store)
        blocker = sqlite3.connect(database_path, isolation_level=None)
        _ = blocker.execute("BEGIN IMMEDIATE")

        # When / Then
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                _ = store.confirm_transition(_transition())
        finally:
            blocker.rollback()
            blocker.close()


def test_manual_qa_reopen_reports_connection_and_transition_state(
    tmp_path: Path,
) -> None:
    # Given
    database_path = tmp_path / "manual-qa.db"
    with SQLiteStore(database_path) as store:
        _seed(store)

        # When
        _ = store.confirm_transition(_transition())

    # Then
    with SQLiteStore(database_path) as reopened:
        diagnostics = reopened.diagnostics()
        event_total = reopened.event_count()
        runtime = reopened.runtime_state("rule-1")
        assert runtime is not None
        summary = (
            f"wal={diagnostics.journal_mode} "
            f"fk={int(diagnostics.foreign_keys_enabled)} "
            f"events={event_total} runtime=1 state={runtime.presence_state.value}"
        )
        _ = sys.stdout.write(f"{summary}\n")
        assert summary == "wal=wal fk=1 events=1 runtime=1 state=inside"
