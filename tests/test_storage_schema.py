from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from custom_components.geofence_journal.storage import (
    DatabaseSchemaError,
    InjectedStorageFaultError,
    SQLiteStore,
    UnsupportedSchemaVersionError,
)

EXPECTED_TABLES = {
    "schema_version",
    "trackers",
    "places",
    "journals",
    "recording_rules",
    "location_events",
    "event_revisions",
    "runtime_states",
}
EXPECTED_INDEXES = {
    "idx_events_journal_time",
    "idx_events_tracker_time",
    "idx_events_place_time",
    "idx_events_status",
    "uq_events_transition",
}


def test_schema_v1_bootstrap_when_database_is_empty(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "journal.db"

    # When
    with SQLiteStore(database_path) as store:
        diagnostics = store.diagnostics()
        tables = store.schema_objects("table")
        indexes = store.schema_objects("index")

    # Then
    assert diagnostics.schema_version == 1
    assert diagnostics.journal_mode == "wal"
    assert diagnostics.foreign_keys_enabled is True
    assert diagnostics.busy_timeout_ms == 1_000
    assert tables >= EXPECTED_TABLES
    assert indexes >= EXPECTED_INDEXES


def test_bootstrap_is_idempotent_when_reopened(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "journal.db"
    with SQLiteStore(database_path) as store:
        original_schema = store.schema_sql()

    # When
    with SQLiteStore(database_path) as reopened:
        reopened_schema = reopened.schema_sql()

    # Then
    assert reopened_schema == original_schema


def test_future_schema_fails_without_mutation(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "future.db"
    with closing(sqlite3.connect(database_path)) as connection:
        _ = connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        _ = connection.execute("INSERT INTO schema_version VALUES (2)")
        _ = connection.execute("CREATE TABLE future_marker(value TEXT NOT NULL)")
        _ = connection.execute("INSERT INTO future_marker VALUES ('untouched')")
        connection.commit()
    before = database_path.read_bytes()

    # When
    with pytest.raises(UnsupportedSchemaVersionError):
        _ = SQLiteStore(database_path).open()

    # Then
    assert database_path.read_bytes() == before


def test_malformed_existing_schema_fails_without_mutation(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "malformed.db"
    with closing(sqlite3.connect(database_path)) as connection:
        _ = connection.execute("CREATE TABLE unrelated(value TEXT NOT NULL)")
        _ = connection.execute("INSERT INTO unrelated VALUES ('keep')")
        connection.commit()
    before = database_path.read_bytes()

    # When
    with pytest.raises(DatabaseSchemaError):
        _ = SQLiteStore(database_path).open()

    # Then
    assert database_path.read_bytes() == before


def test_constraints_reject_invalid_source_and_foreign_key(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "constraints.db"

    with SQLiteStore(database_path):
        pass

    # When / Then
    with closing(sqlite3.connect(database_path)) as connection:
        _ = connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """INSERT INTO places
                (id,name,source_type,exit_margin_m,enabled,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    "p",
                    "Bad",
                    "invalid",
                    50.0,
                    1,
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """INSERT INTO recording_rules
                (id,name,tracker_id,place_id,journal_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    "r",
                    "Bad",
                    "missing",
                    "missing",
                    "missing",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )


def test_schema_contains_complete_v1_role_and_view_contract(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "complete.db"

    # When
    with SQLiteStore(database_path) as store:
        schema = store.schema_sql()

    # Then
    rules_sql = next(sql for sql in schema if "CREATE TABLE recording_rules" in sql)
    events_sql = next(sql for sql in schema if "CREATE TABLE location_events" in sql)
    journals_sql = next(sql for sql in schema if "CREATE TABLE journals" in sql)
    places_sql = next(sql for sql in schema if "CREATE TABLE places" in sql)
    assert {"record_stay", "enter_role", "exit_role"} <= set(rules_sql.split())
    assert "event_role" in events_sql
    assert "visit_count" in journals_sql
    assert "commute" in journals_sql
    assert "'coordinates'" in places_sql


def test_open_creates_nested_database_parent(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / ".storage" / "geofence_journal" / "journal.db"

    # When
    with SQLiteStore(database_path):
        pass

    # Then
    assert database_path.is_file()


def test_injected_bootstrap_failure_leaves_no_partial_schema(tmp_path: Path) -> None:
    # Given
    database_path = tmp_path / "migration-fault.db"

    # When
    with pytest.raises(InjectedStorageFaultError):
        _ = SQLiteStore(database_path).open(inject_migration_failure=True)

    # Then
    with closing(sqlite3.connect(database_path)) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert tables == []
