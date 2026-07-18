"""Runtime lifecycle coordinator for one Geofence Journal config entry."""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, final

import anyio

from .entity_state import (
    DatabaseErrorEntityState,
    GeofenceJournalEntityState,
    HealthyEntityState,
    UnloadedEntityState,
)
from .ha_clock import HomeAssistantClock, HomeAssistantScheduler, UUIDEventIdFactory
from .ha_observer import HomeAssistantTransitionObserver
from .listener import GeofenceTrackerListener, RuleRuntime
from .models import LocationSource
from .runtime.contracts import RuntimeDependencies
from .runtime.engine import RuleTransitionEngine
from .storage.async_adapter import AsyncSQLiteStore
from .storage.errors import StorageError
from .storage.resources import list_active_resources

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .models import Clock
    from .runtime.contracts import Scheduler, TransitionObserver
    from .settings import Settings
    from .storage.resources import ConfiguredResources


@final
class GeofenceJournalManager:
    """Own storage, recovered engines, listener generation, and entity state."""

    def __init__(
        self,
        hass: HomeAssistant,
        settings: Settings,
        *,
        clock: Clock | None = None,
        scheduler: Scheduler | None = None,
    ) -> None:
        """Configure the sole integration runtime without starting I/O."""
        self._hass = hass
        self._settings = settings
        self._store = AsyncSQLiteStore(Path(settings.database_path))
        self._clock = HomeAssistantClock() if clock is None else clock
        self._scheduler = (
            HomeAssistantScheduler(hass) if scheduler is None else scheduler
        )
        self._event_ids = UUIDEventIdFactory()
        self._refresh_lock = anyio.Lock()
        self._runtimes: tuple[RuleRuntime, ...] = ()
        self._listener: GeofenceTrackerListener | None = None
        self._paused = False
        self._opened = False
        self._entity_state: GeofenceJournalEntityState = UnloadedEntityState()
        self._entity_listeners: set[Callable[[], None]] = set()

    @property
    def store(self) -> AsyncSQLiteStore:
        """Expose the typed async store to the management backend adapter."""
        return self._store

    @property
    def settings(self) -> Settings:
        """Return the validated immutable config-entry settings."""
        return self._settings

    @property
    def clock(self) -> Clock:
        """Expose the shared UTC clock to management and export adapters."""
        return self._clock

    @property
    def entity_state(self) -> GeofenceJournalEntityState:
        """Return the current immutable diagnostic snapshot."""
        return self._entity_state

    @property
    def listener_entity_ids(self) -> tuple[str, ...]:
        """Return only the current listener generation's entity IDs."""
        listener = self._listener
        return () if listener is None else listener.entity_ids

    def async_subscribe_entity_state(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Subscribe to immutable diagnostic state replacements."""
        self._entity_listeners.add(listener)

        def unsubscribe() -> None:
            self._entity_listeners.discard(listener)

        return unsubscribe

    async def async_start(self) -> None:
        """Open storage, recover enabled rules, and install the listener."""
        try:
            await self._store.async_open()
            self._opened = True
            self._replace_entity_state(HealthyEntityState(last_event_at=None))
            await self.async_refresh_resources()
        except OSError, sqlite3.Error, StorageError:
            self._replace_entity_state(DatabaseErrorEntityState())
            if self._opened:
                await self._store.async_close()
                self._opened = False
            raise

    async def async_refresh_resources(self) -> None:
        """Replace listeners and recovered engines from enabled DB resources."""
        try:
            async with self._refresh_lock:
                old_runtimes = await self._async_stop_observations_locked()
                if self._paused:
                    return
                resources = await self._store.async_run_operation(list_active_resources)
                active_rule_ids = {
                    str(configured.rule.rule_id) for configured in resources
                }
                for runtime in old_runtimes:
                    if str(runtime.resources.rule.rule_id) not in active_rule_ids:
                        await runtime.engine.async_deactivate()
                await self._async_build_resources_locked(resources)
        except OSError, sqlite3.Error, StorageError:
            self._async_record_database_error()
            raise

    async def async_stop(self) -> None:
        """Stop observations and timers before draining and closing storage."""
        async with self._refresh_lock:
            _ = await self._async_stop_observations_locked()
            if self._opened:
                await self._store.async_close()
                self._opened = False
            self._replace_entity_state(UnloadedEntityState())

    @asynccontextmanager
    async def pause_and_drain(self) -> AsyncGenerator[None]:
        """Pause observations and drain engines around maintenance work."""
        async with self._refresh_lock:
            self._paused = True
            _ = await self._async_stop_observations_locked()
            database_failed = False
            try:
                yield
            except OSError, sqlite3.Error, StorageError:
                database_failed = True
                self._async_record_database_error()
                raise
            finally:
                self._paused = False
                resources = await self._store.async_run_operation(list_active_resources)
                await self._async_build_resources_locked(resources)
                if database_failed:
                    self._async_record_database_error()

    async def _async_stop_observations_locked(self) -> tuple[RuleRuntime, ...]:
        listener = self._listener
        self._listener = None
        if listener is not None:
            await listener.async_stop()
        runtimes = self._runtimes
        for runtime in runtimes:
            await runtime.engine.async_suspend()
        self._runtimes = ()
        return runtimes

    async def _async_build_resources_locked(
        self, resources: tuple[ConfiguredResources, ...]
    ) -> None:
        runtimes: list[RuleRuntime] = []
        for configured in resources:
            engine = RuleTransitionEngine(
                configured.rule,
                self._store,
                RuntimeDependencies(
                    clock=self._clock,
                    scheduler=self._scheduler,
                    event_ids=self._event_ids,
                    source=LocationSource.GPS,
                    store_coordinates=self._settings.store_coordinates,
                    observer=self._transition_observer(configured),
                ),
            )
            await engine.async_recover()
            runtimes.append(RuleRuntime(configured, engine))
        self._runtimes = tuple(runtimes)
        self._update_recovered_last_event()
        listener = GeofenceTrackerListener(
            self._hass, self._runtimes, self._async_record_database_error
        )
        self._listener = listener
        await listener.async_start()

    def _transition_observer(
        self, resources: ConfiguredResources
    ) -> TransitionObserver:
        return HomeAssistantTransitionObserver(
            self._hass, resources, self._async_record_transition
        )

    def _async_record_transition(self, occurred_at: datetime) -> None:
        self._replace_entity_state(HealthyEntityState(last_event_at=occurred_at))

    def _async_record_database_error(self) -> None:
        self._replace_entity_state(DatabaseErrorEntityState())

    def _update_recovered_last_event(self) -> None:
        latest: datetime | None = None
        for runtime in self._runtimes:
            state = runtime.engine.current_state
            if state is None or state.last_event_at is None:
                continue
            if latest is None or state.last_event_at > latest:
                latest = state.last_event_at
        self._replace_entity_state(HealthyEntityState(last_event_at=latest))

    def _replace_entity_state(self, state: GeofenceJournalEntityState) -> None:
        self._entity_state = state
        for listener in tuple(self._entity_listeners):
            listener()
