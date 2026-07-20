"""Runtime lifecycle coordinator for one Geofence Journal config entry."""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from sys import exception as active_exception
from typing import TYPE_CHECKING, assert_never, final

import anyio

from .entity_state import (
    DatabaseErrorEntityState,
    GeofenceJournalEntityState,
    HealthyEntityState,
    UnloadedEntityState,
)
from .generation import (
    HomeAssistantRuntimeDependencyFactory,
    ResourceGeneration,
    async_deactivate_removed,
    async_stage_resource_generation,
    async_suspend_generation,
)
from .ha_clock import HomeAssistantClock, HomeAssistantScheduler, UUIDEventIdFactory
from .lifecycle import (
    RuntimePauseHandle,
    RuntimePauseTokenError,
    attach_secondary_failure,
)
from .storage.async_adapter import AsyncSQLiteStore
from .storage.errors import StorageError
from .storage.events import latest_event_at
from .storage.resources import list_active_resources

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .models import Clock
    from .runtime.contracts import Scheduler
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
        self._dependency_factory = HomeAssistantRuntimeDependencyFactory(
            hass,
            self._clock,
            self._scheduler,
            self._event_ids,
            settings.store_coordinates,
            self.record_event,
        )
        self._refresh_lock = anyio.Lock()
        self._generation: ResourceGeneration | None = None
        self._pause_handles: set[RuntimePauseHandle] = set()
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
        generation = self._generation
        return () if generation is None else generation.entity_ids

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
        started = False
        try:
            await self._store.async_open()
            self._opened = True
            self._replace_entity_state(HealthyEntityState(last_event_at=None))
            await self.async_refresh_resources()
            started = True
        except OSError, sqlite3.Error, StorageError:
            self._replace_entity_state(DatabaseErrorEntityState())
            raise
        finally:
            if not started and self._opened:
                await self._store.async_close()
                self._opened = False

    async def async_refresh_resources(self) -> None:
        """Replace listeners and recovered engines from enabled DB resources."""
        try:
            async with self._refresh_lock:
                if self._pause_handles:
                    return
                resources, staged = await self._async_stage_current_resources_locked()
                old_generation = self._generation
                self._generation = staged
                await async_suspend_generation(old_generation)
                active_rule_ids = frozenset(
                    str(configured.rule.rule_id) for configured in resources
                )
                await async_deactivate_removed(old_generation, active_rule_ids)
        except OSError, sqlite3.Error, StorageError:
            self._async_record_database_error()
            raise

    async def async_stop(self) -> None:
        """Stop observations and timers before draining and closing storage."""
        async with self._refresh_lock:
            await self._async_stop_observations_locked()
            self._pause_handles.clear()
            if self._opened:
                await self._store.async_close()
                self._opened = False
            self._replace_entity_state(UnloadedEntityState())

    async def async_pause(self, reason: str) -> RuntimePauseHandle:
        """Pause observations and return a unique resume capability."""
        handle = RuntimePauseHandle.create(reason=reason)
        async with self._refresh_lock:
            if not self._pause_handles:
                await self._async_stop_observations_locked()
            self._pause_handles.add(handle)
        return handle

    async def async_resume(self, handle: RuntimePauseHandle) -> None:
        """Consume one pause capability and rebuild after the final pause."""
        try:
            async with self._refresh_lock:
                if handle not in self._pause_handles:
                    raise RuntimePauseTokenError(handle)
                if len(self._pause_handles) > 1:
                    self._pause_handles.remove(handle)
                    return
                (
                    _resources,
                    generation,
                ) = await self._async_stage_current_resources_locked()
                self._generation = generation
                self._pause_handles.remove(handle)
        except OSError, sqlite3.Error, StorageError:
            self._async_record_database_error()
            raise

    @asynccontextmanager
    async def pause_and_drain(self) -> AsyncGenerator[None]:
        """Pause observations and drain engines around maintenance work."""
        handle = await self.async_pause("management-maintenance")
        database_failed = False
        try:
            yield
        except OSError, sqlite3.Error, StorageError:
            database_failed = True
            self._async_record_database_error()
            raise
        finally:
            primary_failure = active_exception()
            try:
                with anyio.CancelScope(shield=True):
                    await self.async_resume(handle)
            except (OSError, sqlite3.Error, StorageError, RuntimeError) as failure:
                if primary_failure is None:
                    raise
                attach_secondary_failure(
                    primary_failure,
                    failure,
                    operation="runtime resume",
                )
            if database_failed:
                self._async_record_database_error()

    async def _async_stage_current_resources_locked(
        self,
    ) -> tuple[tuple[ConfiguredResources, ...], ResourceGeneration]:
        resources = await self._store.async_run_operation(list_active_resources)
        generation = await async_stage_resource_generation(
            self._hass,
            self._store,
            resources,
            self._dependency_factory.build,
            self._async_record_database_error,
        )
        completed = False
        try:
            await self._async_update_recovered_last_event()
            completed = True
            return resources, generation
        finally:
            if not completed:
                await async_suspend_generation(generation)

    async def _async_stop_observations_locked(self) -> None:
        generation = self._generation
        self._generation = None
        await async_suspend_generation(generation)

    def record_event(self, occurred_at: datetime) -> None:
        """Publish the latest committed automatic or manual event instant."""
        match self._entity_state:
            case HealthyEntityState(last_event_at=existing):
                if existing is not None and occurred_at <= existing:
                    return
            case DatabaseErrorEntityState() | UnloadedEntityState():
                pass
            case unreachable:
                assert_never(unreachable)
        self._replace_entity_state(HealthyEntityState(last_event_at=occurred_at))

    def _async_record_database_error(self) -> None:
        self._replace_entity_state(DatabaseErrorEntityState())

    async def _async_update_recovered_last_event(self) -> None:
        latest = await self._store.async_run_operation(latest_event_at)
        self._replace_entity_state(HealthyEntityState(last_event_at=latest))

    def _replace_entity_state(self, state: GeofenceJournalEntityState) -> None:
        self._entity_state = state
        for listener in tuple(self._entity_listeners):
            listener()
