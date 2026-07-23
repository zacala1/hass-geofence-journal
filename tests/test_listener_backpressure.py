from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio
from anyio.lowlevel import checkpoint
from custom_components.geofence_journal.listener import (
    GeofenceTrackerListener,
    RuleRuntime,
)
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import State
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    open_runtime_engine,
    runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from homeassistant.core import HomeAssistant

OBSERVED_AT = datetime(2026, 7, 18, 3, tzinfo=UTC)


async def test_listener_coalesces_backlog_to_the_latest_state_per_tracker(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = runtime_resources()
    engine, store, _scheduler = await open_runtime_engine(
        tmp_path / "listener-coalescing.db", resources.rule, RUNTIME_START
    )
    listener = GeofenceTrackerListener(
        hass,
        (RuleRuntime(resources, engine),),
        lambda: None,
    )
    processing_started = anyio.Event()
    release_processing = anyio.Event()
    processed_latitudes: list[float] = []

    async def record_runtime(
        _listener: GeofenceTrackerListener,
        _runtime: RuleRuntime,
        state: State,
    ) -> None:
        latitude = _state_latitude(state)
        processed_latitudes.append(latitude)
        if len(processed_latitudes) == 1:
            processing_started.set()
            await release_processing.wait()

    monkeypatch.setattr(
        GeofenceTrackerListener,
        "_async_process_runtime",
        record_runtime,
    )
    await listener.async_start()

    async with anyio.create_task_group() as tasks:
        _ = tasks.start_soon(
            listener.async_process_state,
            _tracker_state_at_latitude(resources.tracker.entity_id, 1.0),
        )
        await processing_started.wait()
        await listener.async_process_state(
            _tracker_state_at_latitude(resources.tracker.entity_id, 2.0)
        )
        await listener.async_process_state(
            _tracker_state_at_latitude(resources.tracker.entity_id, 3.0)
        )
        release_processing.set()

    await listener.async_stop()
    await store.async_close()

    assert processed_latitudes == [1.0, 3.0]


async def test_listener_stop_drains_inflight_and_discards_queued_state(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = runtime_resources()
    engine, store, _scheduler = await open_runtime_engine(
        tmp_path / "listener-drain.db", resources.rule, RUNTIME_START
    )
    listener = GeofenceTrackerListener(
        hass,
        (RuleRuntime(resources, engine),),
        lambda: None,
    )
    processing_started = anyio.Event()
    release_processing = anyio.Event()
    stop_finished = anyio.Event()
    processed_latitudes: list[float] = []

    async def block_runtime(
        _listener: GeofenceTrackerListener,
        _runtime: RuleRuntime,
        state: State,
    ) -> None:
        processed_latitudes.append(_state_latitude(state))
        processing_started.set()
        await release_processing.wait()

    async def stop_listener() -> None:
        await listener.async_stop()
        stop_finished.set()

    monkeypatch.setattr(
        GeofenceTrackerListener,
        "_async_process_runtime",
        block_runtime,
    )
    await listener.async_start()

    stopped_before_release = False
    async with anyio.create_task_group() as tasks:
        _ = tasks.start_soon(
            listener.async_process_state,
            _tracker_state_at_latitude(resources.tracker.entity_id, 1.0),
        )
        await processing_started.wait()
        _ = tasks.start_soon(
            listener.async_process_state,
            _tracker_state_at_latitude(resources.tracker.entity_id, 2.0),
        )
        await checkpoint()
        _ = tasks.start_soon(stop_listener)
        await checkpoint()
        stopped_before_release = stop_finished.is_set()
        release_processing.set()

    assert stopped_before_release is False
    assert stop_finished.is_set() is True
    assert processed_latitudes == [1.0]
    await store.async_close()


def _tracker_state_at_latitude(entity_id: str, latitude: float) -> State:
    return State(
        entity_id,
        "home",
        {
            ATTR_LATITUDE: latitude,
            ATTR_LONGITUDE: 0.0,
            ATTR_GPS_ACCURACY: 5.0,
        },
        last_updated=OBSERVED_AT,
    )


def _state_latitude(state: State) -> float:
    value: object = state.attributes.get(ATTR_LATITUDE)
    assert isinstance(value, float)
    return value
