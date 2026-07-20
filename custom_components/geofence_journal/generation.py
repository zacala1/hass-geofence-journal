"""Staged resource generation construction and cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ha_confirmation import HomeAssistantConfirmationEvaluator
from .ha_observer import HomeAssistantTransitionObserver
from .listener import GeofenceTrackerListener, RuleRuntime
from .models import LocationSource
from .runtime.contracts import RuntimeDependencies
from .runtime.engine import RuleTransitionEngine

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .models import Clock
    from .runtime.contracts import EventIdFactory, RuntimeStorage, Scheduler
    from .storage.resources import ConfiguredResources


@dataclass(frozen=True, slots=True)
class ResourceGeneration:
    """One fully synchronized listener and its recovered rule engines."""

    runtimes: tuple[RuleRuntime, ...]
    listener: GeofenceTrackerListener

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Return the generation's subscribed tracker entity IDs."""
        return self.listener.entity_ids


@dataclass(frozen=True, slots=True)
class HomeAssistantRuntimeDependencyFactory:
    """Build per-rule engine dependencies from manager-owned capabilities."""

    hass: HomeAssistant
    clock: Clock
    scheduler: Scheduler
    event_ids: EventIdFactory
    store_coordinates: bool
    record_event: Callable[[datetime], None]

    def build(self, resources: ConfiguredResources) -> RuntimeDependencies:
        """Bind one configured rule to current Home Assistant adapters."""
        return RuntimeDependencies(
            clock=self.clock,
            scheduler=self.scheduler,
            event_ids=self.event_ids,
            source=LocationSource.GPS,
            store_coordinates=self.store_coordinates,
            observer=HomeAssistantTransitionObserver(
                self.hass,
                resources,
                self.record_event,
            ),
            confirmation_evaluator=HomeAssistantConfirmationEvaluator(
                self.hass,
                resources,
            ),
        )


async def async_stage_resource_generation(
    hass: HomeAssistant,
    storage: RuntimeStorage,
    resources: tuple[ConfiguredResources, ...],
    dependencies: Callable[[ConfiguredResources], RuntimeDependencies],
    on_database_error: Callable[[], None],
) -> ResourceGeneration:
    """Build and synchronize a generation before publishing it."""
    runtimes: list[RuleRuntime] = []
    listener: GeofenceTrackerListener | None = None
    staged = False
    try:
        for configured in resources:
            engine = RuleTransitionEngine(
                configured.rule,
                storage,
                dependencies(configured),
            )
            await engine.async_recover()
            runtimes.append(RuleRuntime(configured, engine))
        listener = GeofenceTrackerListener(
            hass,
            tuple(runtimes),
            on_database_error,
        )
        await listener.async_start()
        staged = True
        return ResourceGeneration(tuple(runtimes), listener)
    finally:
        if not staged:
            await async_suspend_parts(listener, tuple(runtimes))


async def async_suspend_generation(generation: ResourceGeneration | None) -> None:
    """Stop callbacks and timers owned by one generation."""
    if generation is None:
        return
    await async_suspend_parts(generation.listener, generation.runtimes)


async def async_deactivate_removed(
    generation: ResourceGeneration | None,
    active_rule_ids: frozenset[str],
) -> None:
    """Delete runtime state for rules absent from the committed replacement."""
    if generation is None:
        return
    for runtime in generation.runtimes:
        if str(runtime.resources.rule.rule_id) not in active_rule_ids:
            await runtime.engine.async_deactivate()


async def async_suspend_parts(
    listener: GeofenceTrackerListener | None,
    runtimes: tuple[RuleRuntime, ...],
) -> None:
    """Clean up a partially built generation without publishing it."""
    if listener is not None:
        await listener.async_stop()
    for runtime in runtimes:
        await runtime.engine.async_suspend()
