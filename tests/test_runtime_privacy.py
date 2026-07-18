from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import TYPE_CHECKING, final

import pytest
from custom_components.geofence_journal.models import PresenceState
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    open_runtime_engine,
    recovery_observation,
    runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import (
        SQLConnection,
        SQLiteRow,
    )
    from custom_components.geofence_journal.storage.records import (
        RuntimeStateRecord,
        TransitionResult,
    )


def typed_connection(path: Path) -> SQLConnection:
    """Narrow sqlite3 to the repository's strict structural boundary."""
    return sqlite3.connect(path)


@final
class RecordingObserver:
    """Capture post-commit notifications for outward adapters."""

    def __init__(self) -> None:
        self.calls: list[tuple[TransitionResult, RuntimeStateRecord]] = []

    async def on_transition(
        self, result: TransitionResult, state: RuntimeStateRecord
    ) -> None:
        self.calls.append((result, state))


@pytest.mark.asyncio
async def test_privacy_off_omits_coordinates_but_preserves_accuracy(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "privacy-off.db"
    resources = runtime_resources(enter_seconds=10)
    observer = RecordingObserver()
    engine, store, scheduler = await open_runtime_engine(
        path, resources.rule, RUNTIME_START, observer=observer
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, RUNTIME_START)
    )
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, RUNTIME_START)
    )

    # When
    await scheduler.advance(10)
    await store.async_close()

    # Then
    connection = typed_connection(path)
    row: SQLiteRow | None = connection.execute(
        "SELECT latitude,longitude,accuracy_m FROM location_events"
    ).fetchone()
    connection.close()
    assert row == (None, None, 5.0)
    assert len(observer.calls) == 1
    assert observer.calls[0][0].created is True


@pytest.mark.asyncio
async def test_privacy_on_pending_coordinates_survive_restart_to_event(
    tmp_path: Path,
) -> None:
    # Given
    path = tmp_path / "privacy-restart.db"
    resources = runtime_resources()
    engine, store, scheduler = await open_runtime_engine(
        path, resources.rule, RUNTIME_START, store_coordinates=True
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    await engine.async_observe(
        recovery_observation(PresenceState.OUTSIDE, RUNTIME_START)
    )
    await engine.async_observe(
        recovery_observation(PresenceState.INSIDE, RUNTIME_START)
    )
    await scheduler.advance(60)
    await store.async_close()

    # When
    _engine, reopened, recovered_scheduler = await open_runtime_engine(
        path,
        resources.rule,
        RUNTIME_START + timedelta(seconds=90),
        store_coordinates=True,
    )
    await recovered_scheduler.advance(30)
    await reopened.async_close()

    # Then
    connection = typed_connection(path)
    row: SQLiteRow | None = connection.execute(
        "SELECT latitude,longitude,accuracy_m FROM location_events"
    ).fetchone()
    connection.close()
    assert row == (37.0, 127.0, 5.0)
