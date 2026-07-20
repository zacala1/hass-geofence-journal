from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal.models import (
    Coordinates,
    Meters,
    PresenceState,
)
from custom_components.geofence_journal.storage.errors import DatabaseSchemaError
from custom_components.geofence_journal.storage.records import RuntimeStateRecord
from custom_components.geofence_journal.storage.repository import SQLiteStore
from tests.test_runtime_fixtures import seed_runtime_resources

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def valid_runtime_state() -> RuntimeStateRecord:
    return RuntimeStateRecord(
        rule_id="rule-1",
        presence_state=PresenceState.OUTSIDE,
        last_event_id=None,
        last_event_type=None,
        last_event_at=None,
        enter_cooldown_until=None,
        exit_cooldown_until=None,
        pending_transition=None,
        pending_started_at=None,
        pending_deadline=None,
        pending_generation=0,
        latest_observation_at=NOW,
        latest_coordinates=Coordinates(37.0, 127.0),
        latest_accuracy_m=Meters(5),
        last_processed_at=NOW,
        updated_at=NOW,
    )


def test_runtime_state_round_trips_every_recovery_field(tmp_path: Path) -> None:
    # Given
    state = RuntimeStateRecord(
        rule_id="rule-1",
        presence_state=PresenceState.OUTSIDE,
        last_event_id=None,
        last_event_type=None,
        last_event_at=None,
        enter_cooldown_until=NOW + timedelta(minutes=5),
        exit_cooldown_until=None,
        pending_transition=PresenceState.INSIDE,
        pending_started_at=NOW,
        pending_deadline=NOW + timedelta(minutes=2),
        pending_generation=4,
        latest_observation_at=NOW + timedelta(seconds=30),
        latest_coordinates=Coordinates(37.0, 127.0),
        latest_accuracy_m=Meters(8.0),
        last_processed_at=NOW + timedelta(seconds=30),
        updated_at=NOW + timedelta(seconds=30),
    )
    database = tmp_path / "runtime.db"
    with SQLiteStore(database) as store:
        seed_runtime_resources(store)

        # When
        store.save_runtime_state(state)

        # Then
        assert store.runtime_state("rule-1") == state


def test_runtime_state_delete_is_committed_without_event(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "delete.db"
    with SQLiteStore(database) as store:
        seed_runtime_resources(store)
        state = RuntimeStateRecord(
            rule_id="rule-1",
            presence_state=PresenceState.OUTSIDE,
            last_event_id=None,
            last_event_type=None,
            last_event_at=None,
            enter_cooldown_until=None,
            exit_cooldown_until=None,
            pending_transition=None,
            pending_started_at=None,
            pending_deadline=None,
            pending_generation=0,
            latest_observation_at=NOW,
            latest_coordinates=None,
            latest_accuracy_m=None,
            last_processed_at=NOW,
            updated_at=NOW,
        )
        store.save_runtime_state(state)

        # When
        store.delete_runtime_state("rule-1")

    # Then
    with SQLiteStore(database) as reopened:
        assert reopened.runtime_state("rule-1") is None
        assert reopened.event_count() == 0


def test_failed_runtime_save_rolls_back_and_connection_remains_usable(
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "rollback-save.db"
    invalid = RuntimeStateRecord(
        rule_id="missing-rule",
        presence_state=PresenceState.OUTSIDE,
        last_event_id=None,
        last_event_type=None,
        last_event_at=None,
        enter_cooldown_until=None,
        exit_cooldown_until=None,
        pending_transition=None,
        pending_started_at=None,
        pending_deadline=None,
        pending_generation=0,
        latest_observation_at=NOW,
        latest_coordinates=None,
        latest_accuracy_m=Meters(5),
        last_processed_at=NOW,
        updated_at=NOW,
    )
    with SQLiteStore(database) as store:
        # When
        with pytest.raises(sqlite3.IntegrityError):
            store.save_runtime_state(invalid)

        # Then
        seed_runtime_resources(store)
        store.save_runtime_state(replace(invalid, rule_id="rule-1"))
        assert store.runtime_state("rule-1") is not None


def test_corrupt_persisted_coordinates_are_rejected_on_load(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "corrupt-runtime.db"
    with SQLiteStore(database) as store:
        seed_runtime_resources(store)
        valid = RuntimeStateRecord(
            rule_id="rule-1",
            presence_state=PresenceState.OUTSIDE,
            last_event_id=None,
            last_event_type=None,
            last_event_at=None,
            enter_cooldown_until=None,
            exit_cooldown_until=None,
            pending_transition=None,
            pending_started_at=None,
            pending_deadline=None,
            pending_generation=0,
            latest_observation_at=NOW,
            latest_coordinates=Coordinates(37.0, 127.0),
            latest_accuracy_m=Meters(5),
            last_processed_at=NOW,
            updated_at=NOW,
        )
        store.save_runtime_state(valid)
    connection = sqlite3.connect(database)
    _ = connection.execute("PRAGMA ignore_check_constraints=ON")
    _ = connection.execute(
        "UPDATE runtime_states SET latest_latitude=100 WHERE rule_id='rule-1'"
    )
    connection.commit()
    connection.close()

    # When / Then
    with (
        SQLiteStore(database) as reopened,
        pytest.raises(DatabaseSchemaError, match="out of range"),
    ):
        _ = reopened.runtime_state("rule-1")


@pytest.mark.parametrize(
    ("case_name", "statement", "value", "message"),
    [
        (
            "datetime",
            "UPDATE runtime_states SET latest_observation_at=? WHERE rule_id='rule-1'",
            "not-a-datetime",
            "must be UTC datetime",
        ),
        (
            "latitude",
            "UPDATE runtime_states SET latest_latitude=? WHERE rule_id='rule-1'",
            float("inf"),
            "must be finite",
        ),
        (
            "longitude",
            "UPDATE runtime_states SET latest_longitude=? WHERE rule_id='rule-1'",
            None,
            "must be stored together",
        ),
        (
            "accuracy",
            "UPDATE runtime_states SET latest_accuracy_m=? WHERE rule_id='rule-1'",
            -1.0,
            "must be nonnegative",
        ),
    ],
)
def test_corrupt_runtime_scalars_are_rejected_on_load(
    tmp_path: Path,
    case_name: str,
    statement: str,
    value: str | float | None,
    message: str,
) -> None:
    database = tmp_path / f"corrupt-{case_name}.db"
    with SQLiteStore(database) as store:
        seed_runtime_resources(store)
        store.save_runtime_state(valid_runtime_state())
    with closing(sqlite3.connect(database)) as connection:
        _ = connection.execute("PRAGMA ignore_check_constraints=ON")
        _ = connection.execute(statement, (value,))
        connection.commit()

    with (
        SQLiteStore(database) as reopened,
        pytest.raises(DatabaseSchemaError, match=message),
    ):
        _ = reopened.runtime_state("rule-1")


def test_failed_runtime_delete_rolls_back_and_preserves_state(tmp_path: Path) -> None:
    database = tmp_path / "rollback-delete.db"
    with SQLiteStore(database) as store:
        seed_runtime_resources(store)
        store.save_runtime_state(valid_runtime_state())
        _ = store.run_operation(
            lambda connection: connection.execute(
                """CREATE TRIGGER reject_runtime_delete
                BEFORE DELETE ON runtime_states
                BEGIN SELECT RAISE(ABORT, 'delete fault'); END"""
            )
        )

        with pytest.raises(sqlite3.IntegrityError, match="delete fault"):
            store.delete_runtime_state("rule-1")

        assert store.runtime_state("rule-1") == valid_runtime_state()
