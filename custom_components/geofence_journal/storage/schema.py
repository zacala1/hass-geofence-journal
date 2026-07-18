"""SQLite schema v1 definition and bootstrap."""

from __future__ import annotations

import sqlite3
from typing import Final

from .db_types import SQLConnection, required_integer, required_text
from .errors import DatabaseSchemaError, InjectedStorageFaultError

SCHEMA_VERSION: Final = 1
UTC_CHECK: Final = "CHECK(substr({column}, -1) = 'Z')"

SCHEMA_STATEMENTS: Final[tuple[str, ...]] = (
    "CREATE TABLE schema_version (version INTEGER NOT NULL CHECK(version = 1))",
    """CREATE TABLE trackers (
        id TEXT PRIMARY KEY, entity_id TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL, tracker_kind TEXT NOT NULL
            CHECK(tracker_kind IN ('person','device_tracker')),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
        updated_at TEXT NOT NULL CHECK(substr(updated_at,-1)='Z'))""",
    """CREATE TABLE places (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK(source_type IN ('ha_zone','coordinates')),
        zone_entity_id TEXT, latitude REAL, longitude REAL, radius_m REAL,
        exit_margin_m REAL NOT NULL DEFAULT 50 CHECK(exit_margin_m >= 0),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
        updated_at TEXT NOT NULL CHECK(substr(updated_at,-1)='Z'),
        CHECK((source_type='ha_zone' AND zone_entity_id IS NOT NULL
               AND latitude IS NULL AND longitude IS NULL AND radius_m IS NULL)
           OR (source_type='coordinates' AND zone_entity_id IS NULL
               AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180
               AND radius_m > 0)))""",
    """CREATE TABLE journals (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        view_type TEXT NOT NULL DEFAULT 'presence'
            CHECK(view_type IN ('presence','events','visit_count','commute')),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        retention_days INTEGER CHECK(retention_days IS NULL OR retention_days >= 0),
        created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
        updated_at TEXT NOT NULL CHECK(substr(updated_at,-1)='Z'))""",
    """CREATE TABLE recording_rules (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        tracker_id TEXT NOT NULL, place_id TEXT NOT NULL, journal_id TEXT NOT NULL,
        record_enter INTEGER NOT NULL DEFAULT 1 CHECK(record_enter IN (0,1)),
        record_exit INTEGER NOT NULL DEFAULT 1 CHECK(record_exit IN (0,1)),
        record_stay INTEGER NOT NULL DEFAULT 0 CHECK(record_stay IN (0,1)),
        enter_role TEXT NOT NULL DEFAULT 'generic' CHECK(enter_role='generic'),
        exit_role TEXT NOT NULL DEFAULT 'generic' CHECK(exit_role='generic'),
        enter_confirmation_seconds INTEGER NOT NULL DEFAULT 120
            CHECK(enter_confirmation_seconds >= 0),
        exit_confirmation_seconds INTEGER NOT NULL DEFAULT 180
            CHECK(exit_confirmation_seconds >= 0),
        cooldown_seconds INTEGER NOT NULL DEFAULT 300 CHECK(cooldown_seconds >= 0),
        max_accuracy_m REAL CHECK(max_accuracy_m IS NULL OR max_accuracy_m >= 0),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
        updated_at TEXT NOT NULL CHECK(substr(updated_at,-1)='Z'),
        FOREIGN KEY(tracker_id) REFERENCES trackers(id) ON DELETE RESTRICT,
        FOREIGN KEY(place_id) REFERENCES places(id) ON DELETE RESTRICT,
        FOREIGN KEY(journal_id) REFERENCES journals(id) ON DELETE RESTRICT)""",
    """CREATE TABLE location_events (
        id TEXT PRIMARY KEY, journal_id TEXT NOT NULL, rule_id TEXT,
        tracker_id TEXT NOT NULL, place_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('enter','exit','manual')),
        event_role TEXT NOT NULL DEFAULT 'generic' CHECK(event_role='generic'),
        occurred_at TEXT NOT NULL CHECK(substr(occurred_at,-1)='Z'),
        confirmed_at TEXT NOT NULL CHECK(substr(confirmed_at,-1)='Z'),
        latitude REAL, longitude REAL, accuracy_m REAL,
        source TEXT NOT NULL CHECK(source IN ('gps','ha_zone','manual')),
        status TEXT NOT NULL DEFAULT 'confirmed'
            CHECK(status IN ('confirmed','excluded')),
        note TEXT, original_event_id TEXT, transition_generation INTEGER,
        confirmed_deadline TEXT CHECK(confirmed_deadline IS NULL
            OR substr(confirmed_deadline,-1)='Z'),
        created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
        updated_at TEXT NOT NULL CHECK(substr(updated_at,-1)='Z'),
        FOREIGN KEY(journal_id) REFERENCES journals(id) ON DELETE RESTRICT,
        FOREIGN KEY(rule_id) REFERENCES recording_rules(id) ON DELETE RESTRICT,
        FOREIGN KEY(tracker_id) REFERENCES trackers(id) ON DELETE RESTRICT,
        FOREIGN KEY(place_id) REFERENCES places(id) ON DELETE RESTRICT,
        FOREIGN KEY(original_event_id) REFERENCES location_events(id)
            ON DELETE RESTRICT)""",
    """CREATE TABLE event_revisions (
        id TEXT PRIMARY KEY, event_id TEXT NOT NULL, old_data TEXT NOT NULL,
        new_data TEXT NOT NULL, reason TEXT, changed_by TEXT,
        changed_at TEXT NOT NULL CHECK(substr(changed_at,-1)='Z'),
        FOREIGN KEY(event_id) REFERENCES location_events(id) ON DELETE RESTRICT)""",
    """CREATE TABLE runtime_states (
        rule_id TEXT PRIMARY KEY,
        presence_state TEXT NOT NULL
            CHECK(presence_state IN ('inside','outside','unknown')),
        last_event_id TEXT, last_event_type TEXT
            CHECK(last_event_type IS NULL
                OR last_event_type IN ('enter','exit','manual')),
        last_event_at TEXT CHECK(last_event_at IS NULL OR substr(last_event_at,-1)='Z'),
        enter_cooldown_until TEXT, exit_cooldown_until TEXT,
        pending_transition TEXT CHECK(pending_transition IS NULL
            OR pending_transition IN ('inside','outside')),
        pending_started_at TEXT, pending_deadline TEXT, pending_generation INTEGER,
        latest_observation_at TEXT, last_processed_at TEXT,
        updated_at TEXT NOT NULL CHECK(substr(updated_at,-1)='Z'),
        FOREIGN KEY(rule_id) REFERENCES recording_rules(id) ON DELETE RESTRICT,
        FOREIGN KEY(last_event_id) REFERENCES location_events(id)
            ON DELETE RESTRICT)""",
    "CREATE INDEX idx_events_journal_time ON location_events(journal_id,occurred_at)",
    "CREATE INDEX idx_events_tracker_time ON location_events(tracker_id,occurred_at)",
    "CREATE INDEX idx_events_place_time ON location_events(place_id,occurred_at)",
    "CREATE INDEX idx_events_status ON location_events(status)",
    """CREATE UNIQUE INDEX uq_events_transition
        ON location_events(rule_id,transition_generation,confirmed_deadline)
        WHERE rule_id IS NOT NULL AND transition_generation IS NOT NULL
          AND confirmed_deadline IS NOT NULL""",
)


def bootstrap_v1(connection: SQLConnection, *, inject_failure: bool = False) -> None:
    """Create schema v1 atomically on an empty database."""
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        for position, statement in enumerate(SCHEMA_STATEMENTS):
            _ = connection.execute(statement)
            if inject_failure and position == SCHEMA_VERSION + 1:
                _raise_injected_schema_fault()
        _ = connection.execute(
            "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
        )
    except sqlite3.Error, InjectedStorageFaultError:
        connection.rollback()
        raise
    connection.commit()


def _raise_injected_schema_fault() -> None:
    raise InjectedStorageFaultError(stage="schema-bootstrap")


def read_schema_version(connection: SQLConnection) -> int | None:
    """Return the existing version, or None only for a completely empty file."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {
        required_text(row[0], field="sqlite_master.name")
        for row in rows
        if not required_text(row[0], field="sqlite_master.name").startswith("sqlite_")
    }
    if not names:
        return None
    if "schema_version" not in names:
        raise DatabaseSchemaError(
            detail="existing database has no schema_version table"
        )
    versions = connection.execute("SELECT version FROM schema_version").fetchall()
    if len(versions) != 1:
        raise DatabaseSchemaError(detail="schema_version must contain one integer row")
    return required_integer(versions[0][0], field="schema_version.version")
