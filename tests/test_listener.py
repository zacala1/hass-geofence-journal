from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from custom_components.geofence_journal.listener import (
    GeofenceTrackerListener,
    HomeAssistantZoneLookup,
    RuleRuntime,
    normalize_ha_tracker_state,
)
from custom_components.geofence_journal.location import (
    IgnoredObservation,
    IgnoreReason,
    RawTrackerObservation,
)
from custom_components.geofence_journal.models import TrackerKind, ZonePlace
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import HomeAssistant, State
from tests.test_runtime_fixtures import (
    RUNTIME_START,
    open_runtime_engine,
    runtime_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

OBSERVED_AT = datetime(2026, 7, 18, 3, tzinfo=UTC)


def _tracker_state(entity_id: str) -> State:
    return State(
        entity_id,
        "home",
        {
            ATTR_LATITUDE: 37.5,
            ATTR_LONGITUDE: 127.0,
            ATTR_GPS_ACCURACY: 12,
        },
        last_updated=OBSERVED_AT,
    )


def test_person_and_device_tracker_normalize_to_equivalent_observations() -> None:
    # Given: equivalent person and device_tracker HA states.
    person = _tracker_state("person.alice")
    device = _tracker_state("device_tracker.alice_phone")

    # When: both cross the HA tracker boundary.
    person_result = normalize_ha_tracker_state(person, TrackerKind.PERSON)
    device_result = normalize_ha_tracker_state(device, TrackerKind.DEVICE_TRACKER)

    # Then: only the HA entity domain differs; normalized location data is equal.
    assert person_result == RawTrackerObservation(
        observed_at=OBSERVED_AT,
        latitude=37.5,
        longitude=127.0,
        accuracy_m=12,
        state="home",
    )
    assert device_result == person_result


def test_other_domain_is_rejected_at_ha_boundary() -> None:
    # Given: a sensor state presented as a configured person tracker.
    state = _tracker_state("sensor.alice")

    # When: the state crosses the tracker boundary.
    result = normalize_ha_tracker_state(state, TrackerKind.PERSON)

    # Then: the invalid domain is ignored before domain evaluation.
    assert result == IgnoredObservation(IgnoreReason.INVALID_STATE, OBSERVED_AT)


def test_zone_lookup_reads_fresh_geometry_for_each_sample(
    hass: HomeAssistant,
) -> None:
    # Given: a Home Assistant zone lookup backed by the live state machine.
    lookup = HomeAssistantZoneLookup(hass)
    hass.states.async_set(
        "zone.office",
        "0",
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0, "radius": 100},
    )
    first = lookup.get_zone("zone.office")

    # When: the zone geometry changes before the next lookup.
    hass.states.async_set(
        "zone.office",
        "0",
        {ATTR_LATITUDE: 37.6, ATTR_LONGITUDE: 127.1, "radius": 250},
    )
    second = lookup.get_zone("zone.office")

    # Then: no stale zone snapshot is cached by the adapter.
    assert first is not None
    assert first.latitude == 37.5
    assert first.radius_m == 100
    assert second is not None
    assert second.latitude == 37.6
    assert second.radius_m == 250


def test_zone_lookup_and_number_parser_reject_missing_or_untyped_values(
    hass: HomeAssistant,
) -> None:
    lookup = HomeAssistantZoneLookup(hass)
    invalid = State(
        "person.invalid",
        "home",
        {
            ATTR_LATITUDE: {"not": "a number"},
            ATTR_LONGITUDE: 127.0,
            ATTR_GPS_ACCURACY: 5,
        },
        last_updated=OBSERVED_AT,
    )

    assert lookup.get_zone("zone.missing") is None
    normalized = normalize_ha_tracker_state(invalid, TrackerKind.PERSON)
    assert isinstance(normalized, RawTrackerObservation)
    assert normalized.latitude is None


async def test_listener_handles_duplicate_start_mismatches_and_removed_states(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    resources = runtime_resources()
    engine, store, _scheduler = await open_runtime_engine(
        tmp_path / "listener-branches.db", resources.rule, RUNTIME_START
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    errors: list[str] = []
    listener = GeofenceTrackerListener(
        hass,
        (RuleRuntime(resources, engine),),
        lambda: errors.append("database"),
    )
    matching = _tracker_state("person.fixture")

    await listener.async_process_state(matching)
    await listener.async_start()
    await listener.async_start()
    await listener.async_process_state(_tracker_state("person.other"))
    await listener.async_process_state(
        State(
            "person.fixture",
            "home",
            {ATTR_LATITUDE: "invalid", ATTR_LONGITUDE: 0.0},
            last_updated=OBSERVED_AT,
        )
    )
    hass.states.async_set("person.fixture", "home", matching.attributes)
    await hass.async_block_till_done()
    _ = hass.states.async_remove("person.fixture")
    await hass.async_block_till_done()
    hass.states.async_set("person.fixture", "home", matching.attributes)
    await listener.async_stop()
    await hass.async_block_till_done()
    await store.async_close()

    assert errors == []


async def test_listener_passes_a_missing_zone_as_an_ignored_observation(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    base = runtime_resources()
    resources = replace(
        base,
        place=ZonePlace(
            place_id=base.place.place_id,
            name="Missing zone",
            entity_id="zone.missing",
        ),
    )
    engine, store, _scheduler = await open_runtime_engine(
        tmp_path / "missing-zone-listener.db", resources.rule, RUNTIME_START
    )
    await store.async_upsert_resources(resources, RUNTIME_START)
    listener = GeofenceTrackerListener(
        hass,
        (RuleRuntime(resources, engine),),
        lambda: None,
    )

    await listener.async_start()
    await listener.async_process_state(_tracker_state("person.fixture"))
    await listener.async_stop()
    await store.async_close()

    assert engine.current_state is None
