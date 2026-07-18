"""Config-entry parsing and validated settings contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, override

from .const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DATABASE_PATH,
    CONF_ENTER_CONFIRMATION_SECONDS,
    CONF_EXIT_CONFIRMATION_SECONDS,
    CONF_EXIT_MARGIN_METERS,
    CONF_MAX_GPS_ACCURACY_METERS,
    CONF_STORE_COORDINATES,
)
from .models import Meters, Seconds

if TYPE_CHECKING:
    from collections.abc import Mapping

type ConfigValue = bool | int | float | str


@dataclass(frozen=True, slots=True)
class SettingsFieldError(ValueError):
    """Identify one invalid config-flow setting without exposing its value."""

    field: str

    @override
    def __str__(self) -> str:
        """Render a stable diagnostic without exposing submitted data."""
        return f"invalid setting: {self.field}"


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated configuration stored by the sole config entry."""

    store_coordinates: bool
    enter_confirmation_seconds: Seconds
    exit_confirmation_seconds: Seconds
    cooldown_seconds: Seconds
    exit_margin_meters: Meters
    max_gps_accuracy_meters: Meters
    database_path: str

    def __post_init__(self) -> None:
        """Enforce timing, distance, accuracy, and path invariants."""
        nonnegative = (
            (CONF_ENTER_CONFIRMATION_SECONDS, self.enter_confirmation_seconds),
            (CONF_EXIT_CONFIRMATION_SECONDS, self.exit_confirmation_seconds),
            (CONF_COOLDOWN_SECONDS, self.cooldown_seconds),
            (CONF_EXIT_MARGIN_METERS, self.exit_margin_meters),
        )
        for field, value in nonnegative:
            if value < 0:
                raise SettingsFieldError(field=field)
        if self.max_gps_accuracy_meters <= 0:
            raise SettingsFieldError(field=CONF_MAX_GPS_ACCURACY_METERS)
        if not self.database_path.strip():
            raise SettingsFieldError(field=CONF_DATABASE_PATH)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, ConfigValue]) -> Self:
        """Parse config-entry boundary data into the frozen settings contract."""
        return cls(
            store_coordinates=_read_bool(raw, CONF_STORE_COORDINATES),
            enter_confirmation_seconds=Seconds(
                _read_int(raw, CONF_ENTER_CONFIRMATION_SECONDS)
            ),
            exit_confirmation_seconds=Seconds(
                _read_int(raw, CONF_EXIT_CONFIRMATION_SECONDS)
            ),
            cooldown_seconds=Seconds(_read_int(raw, CONF_COOLDOWN_SECONDS)),
            exit_margin_meters=Meters(_read_float(raw, CONF_EXIT_MARGIN_METERS)),
            max_gps_accuracy_meters=Meters(
                _read_float(raw, CONF_MAX_GPS_ACCURACY_METERS)
            ),
            database_path=_read_str(raw, CONF_DATABASE_PATH),
        )


def _read_bool(raw: Mapping[str, ConfigValue], field: str) -> bool:
    match raw.get(field):
        case bool() as value:
            return value
        case _:
            raise SettingsFieldError(field=field)


def _read_int(raw: Mapping[str, ConfigValue], field: str) -> int:
    match raw.get(field):
        case bool():
            raise SettingsFieldError(field=field)
        case int() as value:
            return value
        case _:
            raise SettingsFieldError(field=field)


def _read_float(raw: Mapping[str, ConfigValue], field: str) -> float:
    match raw.get(field):
        case bool():
            raise SettingsFieldError(field=field)
        case int() | float() as value:
            return float(value)
        case _:
            raise SettingsFieldError(field=field)


def _read_str(raw: Mapping[str, ConfigValue], field: str) -> str:
    match raw.get(field):
        case str() as value:
            return value
        case _:
            raise SettingsFieldError(field=field)
