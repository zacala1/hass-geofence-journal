from __future__ import annotations

from typing import TYPE_CHECKING

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
from homeassistant.helpers import entity_registry
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant


def _integration_entry(database_path: Path) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        data={
            CONF_STORE_COORDINATES: False,
            CONF_ENTER_CONFIRMATION_SECONDS: 0,
            CONF_EXIT_CONFIRMATION_SECONDS: 0,
            CONF_COOLDOWN_SECONDS: 0,
            CONF_EXIT_MARGIN_METERS: 50.0,
            CONF_DATABASE_PATH: str(database_path),
        },
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_real_registry_uses_exact_fixed_entity_ids(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    # Given: a fresh HA registry and a valid Geofence Journal config entry.
    entry = _integration_entry(tmp_path / "entity_ids.db")
    entry.add_to_hass(hass)

    # When: HA loads both real entity platforms into its registry.
    assert await hass.config_entries.async_setup(entry.entry_id)
    try:
        await hass.async_block_till_done()
        registry = entity_registry.async_get(hass)

        # Then: registry IDs have one prefix and retain their fixed metadata.
        actual = {
            registered.entity_id: (
                registered.unique_id,
                registered.object_id_base,
                registered.translation_key,
            )
            for registered in entity_registry.async_entries_for_config_entry(
                registry, entry.entry_id
            )
        }
        assert actual == {
            f"sensor.{DOMAIN}_last_event": (
                f"{DOMAIN}_last_event",
                "last_event",
                "last_event",
            ),
            f"binary_sensor.{DOMAIN}_healthy": (
                f"{DOMAIN}_healthy",
                "healthy",
                "healthy",
            ),
        }
        assert all(hass.states.get(entity_id) is not None for entity_id in actual)
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
