from __future__ import annotations

from typing import Final

import pytest
from custom_components.geofence_journal.const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_MAX_GPS_ACCURACY_METERS,
    CONF_STORE_COORDINATES,
)
from custom_components.geofence_journal.settings import (
    ConfigValue,
    Settings,
    SettingsFieldError,
)

VALID: Final[dict[str, ConfigValue]] = {
    CONF_STORE_COORDINATES: False,
    CONF_ENTER_CONFIRMATION_SECONDS: 1,
    CONF_EXIT_CONFIRMATION_SECONDS: 1,
    CONF_COOLDOWN_SECONDS: 1,
    CONF_EXIT_MARGIN_METERS: 1.0,
    CONF_MAX_GPS_ACCURACY_METERS: 1.0,
    CONF_DATABASE_PATH: "journal.db",
}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (CONF_STORE_COORDINATES, 1),
        (CONF_ENTER_CONFIRMATION_SECONDS, True),
        (CONF_EXIT_CONFIRMATION_SECONDS, 1.5),
        (CONF_EXIT_MARGIN_METERS, False),
        (CONF_MAX_GPS_ACCURACY_METERS, "wide"),
        (CONF_DATABASE_PATH, 7),
    ],
)
def test_mapping_rejects_each_wrong_primitive_type(
    field: str, value: ConfigValue
) -> None:
    raw = dict(VALID)
    raw[field] = value

    with pytest.raises(SettingsFieldError, match=f"invalid setting: {field}"):
        _ = Settings.from_mapping(raw)


def test_settings_reject_an_empty_database_path() -> None:
    raw = dict(VALID)
    raw[CONF_DATABASE_PATH] = "   "

    with pytest.raises(SettingsFieldError, match="database_path"):
        _ = Settings.from_mapping(raw)
