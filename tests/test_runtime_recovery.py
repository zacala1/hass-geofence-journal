from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal.models import PresenceState
from custom_components.geofence_journal.storage.repository import SQLiteStore
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    open_runtime_engine,
    recovery_observation,
    runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

START = RUNTIME_START


@pytest.mark.asyncio
async def test_future_pending_reschedules_for_remaining_restart_delay(
    tmp_path: Path,
) -> None:
    # Given: outside baseline, inside pending at T0, process closes at T+60.
    path = tmp_path / "future-pending.db"
    resources = runtime_resources()
    engine, store, scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, START))
    await engine.async_observe(recovery_observation(PresenceState.INSIDE, START))
    await scheduler.advance(60)
    await store.async_close()

    # When: reopened at T+90 and advanced to the original T+120 deadline.
    _engine, reopened_store, recovered_scheduler = await open_runtime_engine(
        path, resources.rule, START + timedelta(seconds=90)
    )
    await recovered_scheduler.advance(29)
    with SQLiteStore(path) as concurrent:
        assert concurrent.event_count() == 0
    await recovered_scheduler.advance(1)

    # Then: exactly one enter is committed and stable state is inside.
    state = await reopened_store.async_runtime_state("rule-1")
    assert state is not None
    assert state.presence_state is PresenceState.INSIDE
    await reopened_store.async_close()
    with SQLiteStore(path) as final_store:
        assert final_store.event_count() == 1
    print(  # noqa: T201 - required manual recovery evidence summary
        "recovery: OUTSIDE -> pending INSIDE T0 -> close T+60 -> reopen T+90 -> advance T+120 -> one ENTER, stable INSIDE"  # noqa: E501
    )


@pytest.mark.asyncio
async def test_overdue_pending_evaluates_immediately_on_reopen(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "overdue.db"
    resources = runtime_resources()
    engine, store, _scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, START))
    await engine.async_observe(recovery_observation(PresenceState.INSIDE, START))
    await store.async_close()

    # When
    _engine, reopened_store, scheduler = await open_runtime_engine(
        path, resources.rule, START + timedelta(seconds=121)
    )

    # Then
    assert scheduler.calls == []
    await reopened_store.async_close()
    with SQLiteStore(path) as final_store:
        assert final_store.event_count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stable", [PresenceState.INSIDE, PresenceState.OUTSIDE])
async def test_stable_presence_survives_reopen(
    tmp_path: Path, stable: PresenceState
) -> None:
    # Given
    path = tmp_path / f"stable-{stable.value}.db"
    resources = runtime_resources()
    engine, store, _scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(stable, START))
    await store.async_close()

    # When
    _engine, reopened_store, scheduler = await open_runtime_engine(
        path, resources.rule, START + timedelta(minutes=10)
    )

    # Then
    state = await reopened_store.async_runtime_state("rule-1")
    assert state is not None
    assert state.presence_state is stable
    assert scheduler.calls == []
    await reopened_store.async_close()


@pytest.mark.asyncio
async def test_committed_deadline_callback_replay_remains_exactly_once(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "stale-callback.db"
    resources = runtime_resources(enter_seconds=10)
    engine, store, scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, START))
    await engine.async_observe(recovery_observation(PresenceState.INSIDE, START))
    stale = scheduler.calls[0]
    await scheduler.advance(10)

    # When
    await stale.callback()

    # Then
    await store.async_close()
    with SQLiteStore(path) as reopened:
        assert reopened.event_count() == 1


@pytest.mark.asyncio
async def test_deactivate_cancels_pending_and_deletes_recovery_state(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "deactivate.db"
    resources = runtime_resources()
    engine, store, scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, START))
    await engine.async_observe(recovery_observation(PresenceState.INSIDE, START))
    pending_call = scheduler.calls[0]

    # When
    await engine.async_deactivate()

    # Then
    assert not pending_call.active
    assert await store.async_runtime_state("rule-1") is None
    await store.async_close()
