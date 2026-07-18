from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from custom_components.geofence_journal.ha_clock import (
    HomeAssistantClock,
    HomeAssistantScheduledCall,
    HomeAssistantScheduler,
    UUIDEventIdFactory,
)
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def test_clock_cancel_adapter_and_event_ids_are_live() -> None:
    cancelled: list[str] = []
    clock = HomeAssistantClock()
    scheduled = HomeAssistantScheduledCall(lambda: cancelled.append("cancelled"))

    tick = clock.monotonic()
    scheduled.cancel()
    event_id = UUID(UUIDEventIdFactory().next_id())

    assert tick > 0
    assert cancelled == ["cancelled"]
    assert event_id.version == 4


async def test_scheduler_runs_the_async_callback_at_the_deadline(
    hass: HomeAssistant,
) -> None:
    called: list[str] = []
    scheduler = HomeAssistantScheduler(hass)

    async def callback() -> None:
        called.append("due")

    _scheduled = scheduler.schedule(1, callback)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()

    assert called == ["due"]
