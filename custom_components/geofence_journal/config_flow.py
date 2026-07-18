"""Single-entry configuration flow for Geofence Journal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, override

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

if TYPE_CHECKING:
    from collections.abc import Mapping

from .const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_MAX_GPS_ACCURACY_METERS,
    CONF_STORE_COORDINATES,
    DATABASE_FILENAME,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_ENTER_CONFIRMATION_SECONDS,
    DEFAULT_EXIT_CONFIRMATION_SECONDS,
    DEFAULT_EXIT_MARGIN_METERS,
    DEFAULT_MAX_GPS_ACCURACY_METERS,
    DEFAULT_STORE_COORDINATES,
    DOMAIN,
    STORAGE_DIRECTORY,
    STORAGE_INTEGRATION_DIRECTORY,
    TITLE,
)
from .settings import ConfigValue, Settings, SettingsFieldError


class GeofenceJournalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collect the settings for the integration's only config entry."""

    VERSION: Literal[1] = 1
    MINOR_VERSION: Literal[1] = 1

    @override
    async def async_step_user(
        self,
        user_input: Mapping[str, ConfigValue] | None = None,
    ) -> ConfigFlowResult:
        """Create the sole Geofence Journal config entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                settings = Settings.from_mapping(user_input)
            except SettingsFieldError as error:
                errors[error.field] = "invalid_value"
            else:
                return self.async_create_entry(title=TITLE, data=_entry_data(settings))

        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(
                self.hass.config.path(
                    STORAGE_DIRECTORY,
                    STORAGE_INTEGRATION_DIRECTORY,
                    DATABASE_FILENAME,
                )
            ),
            errors=errors,
        )


def _settings_schema(database_path: str) -> vol.Schema:
    """Build a fresh schema because the default path belongs to this hass."""
    return vol.Schema(
        {
            vol.Required(
                CONF_STORE_COORDINATES,
                default=DEFAULT_STORE_COORDINATES,
            ): bool,
            vol.Required(
                CONF_ENTER_CONFIRMATION_SECONDS,
                default=DEFAULT_ENTER_CONFIRMATION_SECONDS,
            ): int,
            vol.Required(
                CONF_EXIT_CONFIRMATION_SECONDS,
                default=DEFAULT_EXIT_CONFIRMATION_SECONDS,
            ): int,
            vol.Required(
                CONF_COOLDOWN_SECONDS,
                default=DEFAULT_COOLDOWN_SECONDS,
            ): int,
            vol.Required(
                CONF_EXIT_MARGIN_METERS,
                default=DEFAULT_EXIT_MARGIN_METERS,
            ): vol.Coerce(float),
            vol.Required(
                CONF_MAX_GPS_ACCURACY_METERS,
                default=DEFAULT_MAX_GPS_ACCURACY_METERS,
            ): vol.Coerce(float),
            vol.Required(CONF_DATABASE_PATH, default=database_path): str,
        }
    )


def _entry_data(settings: Settings) -> dict[str, ConfigValue]:
    """Serialize validated settings into config-entry-safe scalar values."""
    return {
        CONF_STORE_COORDINATES: settings.store_coordinates,
        CONF_ENTER_CONFIRMATION_SECONDS: settings.enter_confirmation_seconds,
        CONF_EXIT_CONFIRMATION_SECONDS: settings.exit_confirmation_seconds,
        CONF_COOLDOWN_SECONDS: settings.cooldown_seconds,
        CONF_EXIT_MARGIN_METERS: settings.exit_margin_meters,
        CONF_MAX_GPS_ACCURACY_METERS: settings.max_gps_accuracy_meters,
        CONF_DATABASE_PATH: settings.database_path,
    }
