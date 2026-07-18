from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

import pytest
from custom_components.geofence_journal.const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_STORE_COORDINATES,
    DOMAIN,
    TITLE,
)
from custom_components.geofence_journal.maintenance import ResourceResponse
from custom_components.geofence_journal.storage import SQLiteStore
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockUser,
    async_capture_events,
    async_fire_time_changed,
)

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from homeassistant.core import HomeAssistant, ServiceResponse

MISSING_ID: Final = "00000000-0000-4000-8000-000000000099"


def _entry(path: Path) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        data={
            CONF_STORE_COORDINATES: False,
            CONF_ENTER_CONFIRMATION_SECONDS: 17,
            CONF_EXIT_CONFIRMATION_SECONDS: 23,
            CONF_COOLDOWN_SECONDS: 29,
            CONF_EXIT_MARGIN_METERS: 37.0,
            CONF_DATABASE_PATH: str(path),
        },
    )


async def _admin_call(
    hass: HomeAssistant,
    user: MockUser,
    action: str,
    data: dict[str, bool | float | int | str],
) -> ServiceResponse:
    return await hass.services.async_call(
        DOMAIN,
        action,
        data,
        blocking=True,
        context=Context(user_id=user.id),
        return_response=True,
    )


async def _upsert(
    hass: HomeAssistant,
    user: MockUser,
    action: str,
    data: dict[str, bool | float | int | str],
) -> UUID:
    response = await _admin_call(hass, user, action, data)
    return ResourceResponse.model_validate(response).resource_id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_admin_actions_build_and_update_one_runnable_rule(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "service-setup.db"
    entry = _entry(database_path)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    events = async_capture_events(hass, "geofence_journal_event")

    try:
        tracker_id = await _upsert(
            hass,
            hass_admin_user,
            "upsert_tracker",
            {
                "entity_id": "person.release_user",
                "kind": "person",
                "name": "Release user",
            },
        )
        place_id = await _upsert(
            hass,
            hass_admin_user,
            "upsert_place",
            {
                "name": "Release home",
                "source_type": "coordinates",
                "latitude": 37.5,
                "longitude": 127.0,
                "radius_meters": 100.0,
            },
        )
        journal_id = await _upsert(
            hass,
            hass_admin_user,
            "upsert_journal",
            {"name": "Release journal"},
        )
        rule_id = await _upsert(
            hass,
            hass_admin_user,
            "upsert_rule",
            {
                "name": "Release presence",
                "tracker_id": str(tracker_id),
                "place_id": str(place_id),
                "journal_id": str(journal_id),
                "max_gps_accuracy_meters": 200.0,
            },
        )
        with SQLiteStore(database_path) as defaults_store:
            stored_defaults = cast(
                "tuple[int, int, int, float] | None",
                defaults_store.run_operation(
                    lambda connection: connection.execute(
                        """SELECT r.enter_confirmation_seconds,
                        r.exit_confirmation_seconds,r.cooldown_seconds,
                        p.exit_margin_m FROM recording_rules r
                        JOIN places p ON p.id=r.place_id WHERE r.id=?""",
                        (str(rule_id),),
                    ).fetchone()
                ),
            )
        assert stored_defaults == (17, 23, 29, 37.0)
        updated_id = await _upsert(
            hass,
            hass_admin_user,
            "upsert_rule",
            {
                "resource_id": str(rule_id),
                "name": "Updated release presence",
                "tracker_id": str(tracker_id),
                "place_id": str(place_id),
                "journal_id": str(journal_id),
                "enter_confirmation_seconds": 0,
                "exit_confirmation_seconds": 0,
                "cooldown_seconds": 0,
                "max_gps_accuracy_meters": 100.0,
            },
        )
        assert updated_id == rule_id
        hass.states.async_set(
            "person.release_user",
            "away",
            {
                ATTR_LATITUDE: 37.51,
                ATTR_LONGITUDE: 127.0,
                ATTR_GPS_ACCURACY: 5,
            },
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "person.release_user",
            "home",
            {
                ATTR_LATITUDE: 37.5,
                ATTR_LONGITUDE: 127.0,
                ATTR_GPS_ACCURACY: 5,
            },
        )
        await hass.async_block_till_done()
        export_response = await _admin_call(
            hass,
            hass_admin_user,
            "export_journal",
            {"journal_id": str(journal_id)},
        )
        assert export_response is not None
        assert export_response["count"] == 1
        async_fire_time_changed(hass, datetime.now(UTC) + timedelta(hours=25))
        await hass.async_block_till_done()
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
    with SQLiteStore(database_path) as store:
        assert store.event_count() == 1
    assert len(events) == 1
    assert events[0].data["event_type"] == "enter"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_invalid_actions_leave_resource_tables_unchanged(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "service-errors.db"
    entry = _entry(database_path)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    try:
        with pytest.raises(ServiceValidationError):
            _ = await _admin_call(
                hass,
                hass_admin_user,
                "upsert_place",
                {
                    "name": "Invalid",
                    "source_type": "coordinates",
                    "latitude": 95.0,
                    "longitude": 127.0,
                    "radius_meters": -1.0,
                },
            )
        with pytest.raises(ServiceValidationError):
            _ = await _admin_call(
                hass,
                hass_admin_user,
                "upsert_tracker",
                {
                    "entity_id": "sensor.wrong_domain",
                    "kind": "person",
                    "name": "Invalid",
                },
            )
        with pytest.raises(ServiceValidationError):
            _ = await _admin_call(
                hass,
                hass_admin_user,
                "upsert_rule",
                {
                    "name": "Broken references",
                    "tracker_id": MISSING_ID,
                    "place_id": MISSING_ID,
                    "journal_id": MISSING_ID,
                },
            )
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
    with SQLiteStore(database_path) as store:
        counts = store.run_operation(
            lambda connection: connection.execute(
                """SELECT (SELECT COUNT(*) FROM trackers),
                (SELECT COUNT(*) FROM places),
                (SELECT COUNT(*) FROM recording_rules)"""
            ).fetchone()
        )
        assert counts == (0, 0, 0)
