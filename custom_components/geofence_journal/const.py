"""Stable constants for Geofence Journal."""

from typing import Final

DOMAIN: Final = "geofence_journal"
TITLE: Final = "Geofence Journal"
VERSION: Final = "0.1.0b2"

CONF_STORE_COORDINATES: Final = "store_coordinates"
CONF_ENTER_CONFIRMATION_SECONDS: Final = "enter_confirmation_seconds"
CONF_EXIT_CONFIRMATION_SECONDS: Final = "exit_confirmation_seconds"
CONF_COOLDOWN_SECONDS: Final = "cooldown_seconds"
CONF_EXIT_MARGIN_METERS: Final = "exit_margin_meters"
CONF_DATABASE_PATH: Final = "database_path"

DEFAULT_STORE_COORDINATES: Final = False
DEFAULT_ENTER_CONFIRMATION_SECONDS: Final = 120
DEFAULT_EXIT_CONFIRMATION_SECONDS: Final = 180
DEFAULT_COOLDOWN_SECONDS: Final = 300
DEFAULT_EXIT_MARGIN_METERS: Final = 50.0
STORAGE_DIRECTORY: Final = ".storage"
STORAGE_INTEGRATION_DIRECTORY: Final = DOMAIN
DATABASE_FILENAME: Final = f"{DOMAIN}.db"
