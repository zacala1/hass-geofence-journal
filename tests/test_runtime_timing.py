from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
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


@pytest.mark.asyncio
async def test_zero_second_confirmation_finishes_without_timer_reentry(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "zero.db"
    resources = runtime_resources(enter_seconds=0)
    engine, store, _scheduler = await open_runtime_engine(
        path, resources.rule, RUNTIME_START
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, RUNTIME_START)
    )

    # When
    with anyio.fail_after(1):
        await engine.async_observe(
            recovery_observation(
                PresenceState.INSIDE, RUNTIME_START + timedelta(seconds=1)
            )
        )

    # Then
    await store.async_close()
    with SQLiteStore(path) as reopened:
        assert reopened.event_count() == 1


@pytest.mark.asyncio
async def test_out_of_order_observation_cannot_replace_latest_sample(
    tmp_path: Path,
) -> None:
    # Given
    resources = runtime_resources()
    engine, store, _scheduler = await open_runtime_engine(
        tmp_path / "stale.db", resources.rule, RUNTIME_START
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    latest = RUNTIME_START + timedelta(seconds=10)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, latest))

    # When
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, RUNTIME_START)
    )

    # Then
    state = await store.async_runtime_state("rule-1")
    assert state is not None
    assert state.pending_transition is None
    assert state.last_processed_at == latest
    await store.async_close()


@pytest.mark.asyncio
async def test_suspend_cancels_timer_but_preserves_pending_for_reload(
    tmp_path: Path,
) -> None:
    # Given
    resources = runtime_resources()
    engine, store, scheduler = await open_runtime_engine(
        tmp_path / "suspend.db", resources.rule, RUNTIME_START
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, RUNTIME_START)
    )
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, RUNTIME_START)
    )
    call = scheduler.calls[0]

    # When
    await engine.async_suspend()

    # Then
    state = await store.async_runtime_state("rule-1")
    assert not call.active
    assert state is not None
    assert state.pending_transition is PresenceState.INSIDE
    await store.async_close()


@pytest.mark.asyncio
async def test_suspended_stale_callback_cannot_touch_closed_storage(
    tmp_path: Path,
) -> None:
    # Given: a callback reference that was already queued before unload.
    path = tmp_path / "suspended-stale.db"
    resources = runtime_resources()
    engine, store, scheduler = await open_runtime_engine(
        path, resources.rule, RUNTIME_START
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, RUNTIME_START)
    )
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, RUNTIME_START)
    )
    stale_callback = scheduler.calls[0].callback

    # When: unload suspends the engine, closes storage, then the old callback runs.
    await engine.async_suspend()
    await store.async_close()
    scheduler.clock.wall += timedelta(seconds=120)
    await stale_callback()

    # Then: the callback is a no-op and pending recovery data remains untouched.
    with SQLiteStore(path) as reopened:
        state = reopened.runtime_state("rule-1")
        assert reopened.event_count() == 0
        assert state is not None
        assert state.pending_transition is PresenceState.INSIDE
