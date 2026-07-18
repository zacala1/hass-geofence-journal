from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final

import pytest
from custom_components.geofence_journal.binary_sensor import HealthyBinarySensor
from custom_components.geofence_journal.const import DOMAIN, TITLE
from custom_components.geofence_journal.entity_state import (
    DatabaseErrorEntityState,
    GeofenceJournalEntityState,
    HealthyEntityState,
    UnloadedEntityState,
)
from custom_components.geofence_journal.sensor import LastEventSensor
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType

if TYPE_CHECKING:
    from collections.abc import Callable


EVENT_AT: Final = datetime(2026, 7, 18, 4, 5, 6, tzinfo=UTC)

type EntityObservation = tuple[datetime | None, bool, bool, bool]


@dataclass(frozen=True, slots=True)
class EntityScenario:
    label: str
    provider_state: GeofenceJournalEntityState
    expected: EntityObservation


@final
class FakeEntityStateProvider:
    """Mutable event-loop fake for deterministic entity state changes."""

    def __init__(self, state: GeofenceJournalEntityState) -> None:
        self._state: GeofenceJournalEntityState = state
        self._listeners: list[Callable[[], None]] = []

    @property
    def entity_state(self) -> GeofenceJournalEntityState:
        return self._state

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def async_subscribe_entity_state(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            self._listeners.remove(listener)

        return unsubscribe

    def set_state(self, state: GeofenceJournalEntityState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener()


def test_fixed_entities_expose_stable_diagnostic_metadata() -> None:
    # Given: a healthy provider and both fixed diagnostic entities.
    provider = FakeEntityStateProvider(HealthyEntityState(last_event_at=EVENT_AT))
    last_event = LastEventSensor(provider)
    healthy = HealthyBinarySensor(provider)

    # When: Home Assistant reads their registry-facing metadata.
    metadata = (
        last_event.unique_id,
        last_event.suggested_object_id,
        last_event.has_entity_name,
        last_event.entity_category,
        last_event.device_class,
        last_event.translation_key,
        healthy.unique_id,
        healthy.suggested_object_id,
        healthy.has_entity_name,
        healthy.entity_category,
        healthy.device_class,
        healthy.translation_key,
    )

    # Then: IDs are fixed by domain and both entities are diagnostics.
    assert metadata == (
        f"{DOMAIN}_last_event",
        "last_event",
        True,
        EntityCategory.DIAGNOSTIC,
        SensorDeviceClass.TIMESTAMP,
        "last_event",
        f"{DOMAIN}_healthy",
        "healthy",
        True,
        EntityCategory.DIAGNOSTIC,
        BinarySensorDeviceClass.CONNECTIVITY,
        "healthy",
    )
    assert last_event.should_poll is healthy.should_poll is False
    assert (
        last_event.device_info
        == healthy.device_info
        == {
            "entry_type": DeviceEntryType.SERVICE,
            "identifiers": {(DOMAIN, DOMAIN)},
            "name": TITLE,
        }
    )


@pytest.mark.parametrize(
    "scenario",
    [
        EntityScenario(
            label="healthy_event",
            provider_state=HealthyEntityState(EVENT_AT),
            expected=(EVENT_AT, True, True, True),
        ),
        EntityScenario(
            label="healthy_no_event",
            provider_state=HealthyEntityState(None),
            expected=(None, True, True, True),
        ),
        EntityScenario(
            label="database_error",
            provider_state=DatabaseErrorEntityState(),
            expected=(None, False, False, True),
        ),
        EntityScenario(
            label="unloaded",
            provider_state=UnloadedEntityState(),
            expected=(None, False, False, False),
        ),
    ],
    ids=["healthy_event", "healthy_no_event", "database_error", "unloaded"],
)
def test_entity_native_values_and_availability_follow_provider_state(
    scenario: EntityScenario,
) -> None:
    # Given: one explicit lifecycle/database state.
    provider = FakeEntityStateProvider(scenario.provider_state)
    last_event = LastEventSensor(provider)
    healthy = HealthyBinarySensor(provider)

    # When: Home Assistant reads native values and availability.
    observed = (
        last_event.native_value,
        last_event.available,
        healthy.is_on,
        healthy.available,
    )
    qa_fields = (
        f"state={scenario.label}",
        f"last_event={observed[0]}",
        f"last_event_available={observed[1]}",
        f"last_event_unique_id={last_event.unique_id}",
        f"healthy={observed[2]}",
        f"healthy_available={observed[3]}",
        f"healthy_unique_id={healthy.unique_id}",
    )
    _ = sys.stdout.write(f"MANUAL_QA {' '.join(qa_fields)}\n")

    # Then: no event stays available/unknown, DB failure is visible, unload is not.
    assert observed == scenario.expected


async def test_subscription_is_replaced_without_stale_reload_listeners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one provider and two entity instances representing unload/reload.
    provider = FakeEntityStateProvider(HealthyEntityState(last_event_at=None))
    first = LastEventSensor(provider)
    reloaded = LastEventSensor(provider)
    writes: list[str] = []
    removers: list[Callable[[], None]] = []
    monkeypatch.setattr(first, "async_write_ha_state", lambda: writes.append("first"))
    monkeypatch.setattr(first, "async_on_remove", removers.append)
    monkeypatch.setattr(
        reloaded, "async_write_ha_state", lambda: writes.append("reloaded")
    )
    monkeypatch.setattr(reloaded, "async_on_remove", removers.append)

    # When: an update occurs before unload and another after a clean reload.
    await first.async_added_to_hass()
    provider.set_state(HealthyEntityState(last_event_at=EVENT_AT))
    removers.pop()()
    await reloaded.async_added_to_hass()
    provider.set_state(DatabaseErrorEntityState())
    removers.pop()()

    # Then: each update reaches only the currently loaded entity and leaves no listener.
    assert writes == ["first", "reloaded"]
    assert first.native_value == EVENT_AT
    assert reloaded.available is False
    assert provider.listener_count == 0
