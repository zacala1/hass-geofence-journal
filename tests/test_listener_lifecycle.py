from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal import listener as listener_module
from custom_components.geofence_journal.listener import (
    GeofenceTrackerListener,
    RuleRuntime,
)
from custom_components.geofence_journal.storage.errors import StorageClosedError
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    open_runtime_engine,
    runtime_resources,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from pathlib import Path

    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.event import EventStateChangedData


async def test_start_failure_unregisters_listener_generation(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a registered listener whose initial state sync reaches closed storage.
    resources = runtime_resources()
    engine, store, _scheduler = await open_runtime_engine(
        tmp_path / "failed-listener-start.db", resources.rule, RUNTIME_START
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    await store.async_close()
    hass.states.async_set(
        resources.tracker.entity_id,
        "home",
        {
            ATTR_LATITUDE: 0.0,
            ATTR_LONGITUDE: 0.0,
            ATTR_GPS_ACCURACY: 5.0,
        },
    )
    removal_calls: list[str] = []

    def register_listener(
        _hass: HomeAssistant,
        entity_ids: str | Iterable[str],
        _action: Callable[[Event[EventStateChangedData]], Awaitable[None]],
    ) -> Callable[[], None]:
        assert tuple(entity_ids) == (resources.tracker.entity_id,)

        def remove_listener() -> None:
            removal_calls.append("removed")

        return remove_listener

    monkeypatch.setattr(
        listener_module, "async_track_state_change_event", register_listener
    )
    listener = GeofenceTrackerListener(
        hass,
        (RuleRuntime(resources, engine),),
        lambda: None,
    )

    # When: startup registers the callback and initial synchronization fails.
    with pytest.raises(StorageClosedError):
        await listener.async_start()

    # Then: registration is removed once and the failed generation stays inactive.
    assert removal_calls == ["removed"]
    state_after_failure = engine.current_state
    current_state = hass.states.get(resources.tracker.entity_id)
    assert current_state is not None
    await listener.async_process_state(current_state)
    assert engine.current_state is state_after_failure
