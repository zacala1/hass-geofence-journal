from __future__ import annotations

from datetime import datetime, timedelta
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
async def test_opposite_transition_bypasses_active_direction_cooldown(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "opposite-cooldown.db"
    resources = runtime_resources(
        enter_seconds=10, exit_seconds=10, cooldown_seconds=100
    )
    engine, store, scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, START))
    await engine.async_observe(recovery_observation(PresenceState.INSIDE, START))
    await scheduler.advance(10)

    # When
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, scheduler.clock.wall)
    )
    await scheduler.advance(10)

    # Then
    await store.async_close()
    with SQLiteStore(path) as reopened:
        assert reopened.event_count() == 2
        state = reopened.runtime_state("rule-1")
        assert state is not None
        assert state.presence_state is PresenceState.OUTSIDE


@pytest.mark.asyncio
async def test_same_direction_transition_is_suppressed_during_cooldown(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "same-cooldown.db"
    resources = runtime_resources(
        enter_seconds=10, exit_seconds=10, cooldown_seconds=100
    )
    engine, store, scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, START))
    await engine.async_observe(recovery_observation(PresenceState.INSIDE, START))
    await scheduler.advance(10)
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, scheduler.clock.wall)
    )
    await scheduler.advance(10)

    # When
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, scheduler.clock.wall)
    )
    await scheduler.advance(10)

    # Then
    state = await store.async_runtime_state("rule-1")
    assert state is not None
    assert state.presence_state is PresenceState.INSIDE
    await store.async_close()
    with SQLiteStore(path) as reopened:
        assert reopened.event_count() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("elapsed", "expected_deadline"),
    [
        (309, START + timedelta(seconds=310)),
        (310, None),
        (311, None),
    ],
)
async def test_recovery_retains_only_active_cooldown(
    tmp_path: Path, elapsed: int, expected_deadline: datetime | None
) -> None:
    # Given
    path = tmp_path / f"cooldown-{elapsed}.db"
    resources = runtime_resources(enter_seconds=10, cooldown_seconds=300)
    engine, store, scheduler = await open_runtime_engine(path, resources.rule, START)
    await store.async_upsert_resources(resources, START)
    await engine.async_observe(recovery_observation(PresenceState.OUTSIDE, START))
    await engine.async_observe(recovery_observation(PresenceState.INSIDE, START))
    await scheduler.advance(10)
    await store.async_close()

    # When
    _engine, reopened_store, _scheduler = await open_runtime_engine(
        path, resources.rule, START + timedelta(seconds=elapsed)
    )

    # Then
    state = await reopened_store.async_runtime_state("rule-1")
    assert state is not None
    assert state.enter_cooldown_until == expected_deadline
    await reopened_store.async_close()
