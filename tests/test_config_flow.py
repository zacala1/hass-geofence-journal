from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from custom_components.geofence_journal.const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_MAX_GPS_ACCURACY_METERS,
    CONF_STORE_COORDINATES,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_ENTER_CONFIRMATION_SECONDS,
    DEFAULT_EXIT_CONFIRMATION_SECONDS,
    DEFAULT_EXIT_MARGIN_METERS,
    DEFAULT_MAX_GPS_ACCURACY_METERS,
    DOMAIN,
    TITLE,
)
from custom_components.geofence_journal.settings import Settings

EXPECTED_DB_SUFFIX: Final = Path(".storage/geofence_journal/geofence_journal.db")


def _valid_input(hass: HomeAssistant) -> dict[str, bool | int | float | str]:
    return {
        CONF_STORE_COORDINATES: False,
        CONF_ENTER_CONFIRMATION_SECONDS: DEFAULT_ENTER_CONFIRMATION_SECONDS,
        CONF_EXIT_CONFIRMATION_SECONDS: DEFAULT_EXIT_CONFIRMATION_SECONDS,
        CONF_COOLDOWN_SECONDS: DEFAULT_COOLDOWN_SECONDS,
        CONF_EXIT_MARGIN_METERS: DEFAULT_EXIT_MARGIN_METERS,
        CONF_MAX_GPS_ACCURACY_METERS: DEFAULT_MAX_GPS_ACCURACY_METERS,
        CONF_DATABASE_PATH: hass.config.path(*EXPECTED_DB_SUFFIX.parts),
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_creates_typed_default_settings(
    hass: HomeAssistant,
) -> None:
    # Given: a fresh Home Assistant instance with no integration entry.
    initial = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    # When: the user accepts every displayed default.
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"],
        _valid_input(hass),
    )

    # Then: one entry is created and its boundary data parses to Settings.
    assert result.get("type") is FlowResultType.CREATE_ENTRY
    assert result.get("title") == TITLE
    result_data = result.get("data")
    assert result_data is not None
    settings = Settings.from_mapping(result_data)
    assert settings.store_coordinates is False
    assert Path(settings.database_path).parts[-3:] == EXPECTED_DB_SUFFIX.parts[-3:]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_aborts_when_entry_already_exists(
    hass: HomeAssistant,
) -> None:
    # Given: the sole supported config entry already exists.
    MockConfigEntry(domain=DOMAIN, title=TITLE, data={}).add_to_hass(hass)

    # When: a second user flow starts.
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    # Then: Home Assistant reports the single-instance abort.
    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "single_instance_allowed"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_restarts_after_interruption(hass: HomeAssistant) -> None:
    # Given: a user abandons an unsubmitted configuration flow.
    initial = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    hass.config_entries.flow.async_abort(initial["flow_id"])

    # When: configuration is started again.
    restarted = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    # Then: stale flow state does not block a fresh form.
    assert restarted.get("type") is FlowResultType.FORM


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_repeated_flow_aborts_after_first_flow_creates_entry(
    hass: HomeAssistant,
) -> None:
    # Given: two forms were opened before either one created the sole entry.
    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    repeated = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    _ = await hass.config_entries.flow.async_configure(
        first["flow_id"], _valid_input(hass)
    )

    # When: the stale repeated form is submitted.
    # Then: HA reports that it removed the flow when the entry was created.
    with pytest.raises(UnknownFlow):
        _ = await hass.config_entries.flow.async_configure(
            repeated["flow_id"], _valid_input(hass)
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        (CONF_ENTER_CONFIRMATION_SECONDS, -1),
        (CONF_EXIT_CONFIRMATION_SECONDS, -1),
        (CONF_COOLDOWN_SECONDS, -1),
        (CONF_EXIT_MARGIN_METERS, -0.1),
        (CONF_MAX_GPS_ACCURACY_METERS, 0),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_keeps_form_open_for_invalid_numeric_setting(
    hass: HomeAssistant,
    field: str,
    invalid_value: float,
) -> None:
    # Given: an active user flow and otherwise valid input.
    initial = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    user_input = _valid_input(hass)
    user_input[field] = invalid_value

    # When: the invalid boundary value is submitted.
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], user_input
    )

    # Then: no entry is created and the offending field is identified.
    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") == {field: "invalid_value"}
