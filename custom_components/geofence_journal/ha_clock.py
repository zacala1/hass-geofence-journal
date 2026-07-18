"""Home Assistant clock, monotonic scheduler, and UUID adapters."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, final
from uuid import uuid4

from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant


@final
class HomeAssistantClock:
    """Read timezone-aware UTC and process-local monotonic time."""

    def utc_now(self) -> datetime:
        """Return Home Assistant's current UTC instant."""
        return dt_util.utcnow()

    def monotonic(self) -> float:
        """Return process-local monotonic seconds."""
        return monotonic()


@dataclass(frozen=True, slots=True)
class HomeAssistantScheduledCall:
    """Wrap a Home Assistant timer cancellation callback."""

    cancel_callback: Callable[[], None]

    def cancel(self) -> None:
        """Cancel the scheduled callback if it has not fired."""
        self.cancel_callback()


@final
class HomeAssistantScheduler:
    """Schedule engine deadlines on Home Assistant's monotonic event loop."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind scheduling to one Home Assistant event loop."""
        self._hass = hass

    def schedule(
        self, delay_seconds: float, callback: Callable[[], Awaitable[None]]
    ) -> HomeAssistantScheduledCall:
        """Schedule an async callback after a monotonic delay."""

        async def run(_now: datetime) -> None:
            await callback()

        return HomeAssistantScheduledCall(
            async_call_later(self._hass, delay_seconds, run)
        )


@final
class UUIDEventIdFactory:
    """Generate random UUID identifiers for persisted transitions."""

    def next_id(self) -> str:
        """Return one canonical UUID string."""
        return str(uuid4())
