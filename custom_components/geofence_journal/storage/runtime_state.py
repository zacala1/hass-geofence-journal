"""Persistence operations for restart-safe per-rule runtime state."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from math import isfinite
from typing import Final, assert_never

from custom_components.geofence_journal.models import (
    Coordinates,
    LocationEventType,
    Meters,
    PresenceState,
)

from .db_types import SQLConnection, SQLiteValue, required_integer, required_text
from .errors import DatabaseSchemaError
from .records import RuntimeStateRecord, utc_text

MIN_LATITUDE: Final = -90.0
MAX_LATITUDE: Final = 90.0
MIN_LONGITUDE: Final = -180.0
MAX_LONGITUDE: Final = 180.0


def save_runtime_state(connection: SQLConnection, state: RuntimeStateRecord) -> None:
    """Replace one runtime row as a committed transaction."""
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        write_runtime_state_row(connection, state)
    except sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()


def load_runtime_state(
    connection: SQLConnection, rule_id: str
) -> RuntimeStateRecord | None:
    """Load all persisted recovery fields for one rule."""
    row = connection.execute(
        """SELECT presence_state,last_event_id,last_event_type,last_event_at,
        enter_cooldown_until,exit_cooldown_until,pending_transition,
        pending_started_at,pending_deadline,pending_generation,
        latest_observation_at,latest_latitude,latest_longitude,latest_accuracy_m,
        last_processed_at,updated_at
        FROM runtime_states WHERE rule_id=?""",
        (rule_id,),
    ).fetchone()
    if row is None:
        return None
    return RuntimeStateRecord(
        rule_id=rule_id,
        presence_state=PresenceState(required_text(row[0], field="presence_state")),
        last_event_id=_optional_text(row[1], field="last_event_id"),
        last_event_type=_optional_event_type(row[2]),
        last_event_at=_optional_datetime(row[3], field="last_event_at"),
        enter_cooldown_until=_optional_datetime(row[4], field="enter_cooldown_until"),
        exit_cooldown_until=_optional_datetime(row[5], field="exit_cooldown_until"),
        pending_transition=_optional_presence(row[6]),
        pending_started_at=_optional_datetime(row[7], field="pending_started_at"),
        pending_deadline=_optional_datetime(row[8], field="pending_deadline"),
        pending_generation=(
            0
            if row[9] is None
            else required_integer(row[9], field="pending_generation")
        ),
        latest_observation_at=_optional_datetime(
            row[10], field="latest_observation_at"
        ),
        latest_coordinates=_optional_coordinates(row[11], row[12]),
        latest_accuracy_m=_optional_accuracy(row[13]),
        last_processed_at=_optional_datetime(row[14], field="last_processed_at"),
        updated_at=datetime.fromisoformat(required_text(row[15], field="updated_at")),
    )


def delete_runtime_state(connection: SQLConnection, rule_id: str) -> None:
    """Delete a runtime row transactionally without creating an event."""
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        _ = connection.execute("DELETE FROM runtime_states WHERE rule_id=?", (rule_id,))
    except sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()


def delete_inactive_runtime_states(connection: SQLConnection) -> None:
    """Delete recovery rows whose complete resource linkage is inactive."""
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        _ = connection.execute(
            """DELETE FROM runtime_states
            WHERE rule_id NOT IN (
                SELECT r.id FROM recording_rules r
                JOIN trackers t ON t.id=r.tracker_id
                JOIN places p ON p.id=r.place_id
                JOIN journals j ON j.id=r.journal_id
                WHERE r.enabled=1 AND t.enabled=1 AND p.enabled=1 AND j.enabled=1
            )"""
        )
    except sqlite3.Error:
        connection.rollback()
        raise
    connection.commit()


def write_runtime_state_row(
    connection: SQLConnection, state: RuntimeStateRecord
) -> None:
    """Write one runtime row inside the caller's active transaction."""
    _ = connection.execute(
        """INSERT INTO runtime_states
        (rule_id,presence_state,last_event_id,last_event_type,last_event_at,
         enter_cooldown_until,exit_cooldown_until,pending_transition,
         pending_started_at,pending_deadline,pending_generation,
         latest_observation_at,latest_latitude,latest_longitude,latest_accuracy_m,
         last_processed_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(rule_id) DO UPDATE SET
        presence_state=excluded.presence_state,last_event_id=excluded.last_event_id,
        last_event_type=excluded.last_event_type,last_event_at=excluded.last_event_at,
        enter_cooldown_until=excluded.enter_cooldown_until,
        exit_cooldown_until=excluded.exit_cooldown_until,
        pending_transition=excluded.pending_transition,
        pending_started_at=excluded.pending_started_at,
        pending_deadline=excluded.pending_deadline,
        pending_generation=excluded.pending_generation,
        latest_observation_at=excluded.latest_observation_at,
        latest_latitude=excluded.latest_latitude,
        latest_longitude=excluded.latest_longitude,
        latest_accuracy_m=excluded.latest_accuracy_m,
        last_processed_at=excluded.last_processed_at,updated_at=excluded.updated_at""",
        (
            state.rule_id,
            state.presence_state.value,
            state.last_event_id,
            None if state.last_event_type is None else state.last_event_type.value,
            _datetime_text(state.last_event_at),
            _datetime_text(state.enter_cooldown_until),
            _datetime_text(state.exit_cooldown_until),
            (
                None
                if state.pending_transition is None
                else state.pending_transition.value
            ),
            _datetime_text(state.pending_started_at),
            _datetime_text(state.pending_deadline),
            state.pending_generation,
            _datetime_text(state.latest_observation_at),
            None
            if state.latest_coordinates is None
            else state.latest_coordinates.latitude,
            None
            if state.latest_coordinates is None
            else state.latest_coordinates.longitude,
            state.latest_accuracy_m,
            _datetime_text(state.last_processed_at),
            utc_text(state.updated_at),
        ),
    )


def _datetime_text(value: datetime | None) -> str | None:
    return None if value is None else utc_text(value)


def _optional_text(value: SQLiteValue, *, field: str) -> str | None:
    return None if value is None else required_text(value, field=field)


def _optional_datetime(value: SQLiteValue, *, field: str) -> datetime | None:
    text = _optional_text(value, field=field)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise DatabaseSchemaError(detail=f"{field} must be UTC datetime") from error
    _ = utc_text(parsed)
    return parsed


def _optional_event_type(value: SQLiteValue) -> LocationEventType | None:
    text = _optional_text(value, field="last_event_type")
    return None if text is None else LocationEventType(text)


def _optional_presence(value: SQLiteValue) -> PresenceState | None:
    text = _optional_text(value, field="pending_transition")
    return None if text is None else PresenceState(text)


def _optional_meters(value: SQLiteValue, *, field: str) -> Meters | None:
    match value:
        case None:
            return None
        case int() | float() as number:
            parsed = float(number)
            if not isfinite(parsed):
                raise DatabaseSchemaError(detail=f"{field} must be finite")
            return Meters(parsed)
        case str() | bytes():
            raise DatabaseSchemaError(detail=f"{field} must be REAL")
        case unreachable:
            assert_never(unreachable)


def _optional_coordinates(
    latitude: SQLiteValue, longitude: SQLiteValue
) -> Coordinates | None:
    parsed_latitude = _optional_meters(latitude, field="latest_latitude")
    parsed_longitude = _optional_meters(longitude, field="latest_longitude")
    if parsed_latitude is None and parsed_longitude is None:
        return None
    if parsed_latitude is None or parsed_longitude is None:
        raise DatabaseSchemaError(detail="latest coordinates must be stored together")
    if not MIN_LATITUDE <= parsed_latitude <= MAX_LATITUDE or not (
        MIN_LONGITUDE <= parsed_longitude <= MAX_LONGITUDE
    ):
        raise DatabaseSchemaError(detail="latest coordinates are out of range")
    return Coordinates(float(parsed_latitude), float(parsed_longitude))


def _optional_accuracy(value: SQLiteValue) -> Meters | None:
    accuracy = _optional_meters(value, field="latest_accuracy_m")
    if accuracy is not None and accuracy < 0:
        raise DatabaseSchemaError(detail="latest_accuracy_m must be nonnegative")
    return accuracy
