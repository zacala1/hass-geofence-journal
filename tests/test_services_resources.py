from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    RuleDefinition,
    RuleId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.storage import (
    AsyncSQLiteStore,
    ConfiguredResources,
    SQLiteStore,
    resources,
)
from custom_components.geofence_journal.storage.schema import bootstrap_v1

if TYPE_CHECKING:
    from pathlib import Path

    from custom_components.geofence_journal.storage.db_types import (
        SQLConnection,
        SQLiteParameters,
        SQLiteRow,
    )

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")
RULE_ID: Final = RuleId("00000000-0000-4000-8000-000000000004")


def _single_row(
    connection: SQLConnection,
    sql: str,
    parameters: SQLiteParameters = (),
) -> SQLiteRow | None:
    return connection.execute(sql, parameters).fetchone()


def test_storage_exposes_active_resource_loader() -> None:
    # Given: Task 5 needs a typed restart/listener refresh query.
    required_names = {"list_active_resources"}

    # When: the resource module contract is inspected.
    exposed_names = set(dir(resources))

    # Then: loader wiring does not require repository internals.
    assert required_names <= exposed_names


def _resource_models() -> tuple[
    TrackerDefinition, CoordinatePlace, JournalDefinition, RuleDefinition
]:
    return (
        TrackerDefinition(
            tracker_id=TRACKER_ID,
            entity_id="person.alice",
            kind=TrackerKind.PERSON,
            name="Alice",
            enabled=True,
        ),
        CoordinatePlace(
            place_id=PLACE_ID,
            name="Home",
            center=Coordinates(latitude=37.5, longitude=127.0),
            radius_m=Meters(100),
        ),
        JournalDefinition(journal_id=JOURNAL_ID, name="Presence", enabled=True),
        RuleDefinition(
            rule_id=RULE_ID,
            tracker_id=TRACKER_ID,
            place_id=PLACE_ID,
            journal_id=JOURNAL_ID,
            enabled=True,
            enter_confirmation_seconds=Seconds(10),
            exit_confirmation_seconds=Seconds(20),
            cooldown_seconds=Seconds(30),
            exit_margin_meters=Meters(999),
            max_gps_accuracy_meters=Meters(50),
        ),
    )


def test_place_owned_exit_margin_round_trips_into_active_rule(tmp_path: Path) -> None:
    # Given: one complete enabled linkage and a non-default place exit margin.
    tracker, place, journal, rule = _resource_models()
    with closing(sqlite3.connect(tmp_path / "resources.db")) as connection:
        bootstrap_v1(connection)
        _ = connection.execute("PRAGMA foreign_keys=ON")
        resources.upsert_tracker(connection, tracker, NOW)
        resources.upsert_place(connection, place, NOW, exit_margin_meters=Meters(25))
        resources.upsert_journal(connection, journal, NOW)
        resources.upsert_rule(connection, rule, NOW, name="Home presence")

        # When: Task 5 refreshes its typed enabled-resource view.
        active = resources.list_active_resources(connection)

        # Then: the physical place column is authoritative for evaluator rules.
        stored_margin = _single_row(
            connection,
            "SELECT exit_margin_m FROM places WHERE id=?",
            (PLACE_ID,),
        )
        assert stored_margin == (25.0,)
        assert len(active) == 1
        assert active[0].rule.exit_margin_meters == Meters(25)
        assert active[0].tracker.entity_id == "person.alice"


def test_active_resource_loader_omits_disabled_linkage(tmp_path: Path) -> None:
    # Given: a complete linkage whose tracker is disabled before refresh.
    tracker, place, journal, rule = _resource_models()
    disabled = TrackerDefinition(
        tracker_id=tracker.tracker_id,
        entity_id=tracker.entity_id,
        kind=tracker.kind,
        name=tracker.name,
        enabled=False,
    )
    with closing(sqlite3.connect(tmp_path / "disabled.db")) as connection:
        bootstrap_v1(connection)
        _ = connection.execute("PRAGMA foreign_keys=ON")
        resources.upsert_tracker(connection, disabled, NOW)
        resources.upsert_place(connection, place, NOW)
        resources.upsert_journal(connection, journal, NOW)
        resources.upsert_rule(connection, rule, NOW)

        # When: active resources are queried.
        active = resources.list_active_resources(connection)

        # Then: Task 5 will install no listener for the disabled linkage.
        assert active == ()


def test_rule_upsert_with_broken_reference_writes_no_rule(tmp_path: Path) -> None:
    # Given: a v1 database with foreign keys enabled but no referenced resources.
    *_, rule = _resource_models()
    with closing(sqlite3.connect(tmp_path / "broken.db")) as connection:
        bootstrap_v1(connection)
        _ = connection.execute("PRAGMA foreign_keys=ON")

        # When: a broken linkage reaches the storage constraint.
        with pytest.raises(sqlite3.IntegrityError):
            resources.upsert_rule(connection, rule, NOW)

        # Then: the failed statement leaves no partial rule row.
        count = _single_row(connection, "SELECT COUNT(*) FROM recording_rules")
        assert count == (0,)


def test_rule_update_replaces_tracker_reference(tmp_path: Path) -> None:
    # Given: a persisted rule and a second valid tracker.
    tracker, place, journal, rule = _resource_models()
    replacement_id = TrackerId("00000000-0000-4000-8000-000000000005")
    replacement = TrackerDefinition(
        tracker_id=replacement_id,
        entity_id="device_tracker.alice_phone",
        kind=TrackerKind.DEVICE_TRACKER,
        name="Alice phone",
        enabled=True,
    )
    updated_rule = RuleDefinition(
        rule_id=rule.rule_id,
        tracker_id=replacement_id,
        place_id=rule.place_id,
        journal_id=rule.journal_id,
        enabled=rule.enabled,
        enter_confirmation_seconds=rule.enter_confirmation_seconds,
        exit_confirmation_seconds=rule.exit_confirmation_seconds,
        cooldown_seconds=rule.cooldown_seconds,
        exit_margin_meters=rule.exit_margin_meters,
        max_gps_accuracy_meters=rule.max_gps_accuracy_meters,
    )
    with closing(sqlite3.connect(tmp_path / "update.db")) as connection:
        bootstrap_v1(connection)
        _ = connection.execute("PRAGMA foreign_keys=ON")
        resources.upsert_tracker(connection, tracker, NOW)
        resources.upsert_tracker(connection, replacement, NOW)
        resources.upsert_place(connection, place, NOW)
        resources.upsert_journal(connection, journal, NOW)
        resources.upsert_rule(connection, rule, NOW)

        # When: the stable rule UUID is upserted with a new tracker.
        resources.upsert_rule(connection, updated_rule, NOW)

        # Then: the same row and active typed linkage use the new reference.
        active = resources.list_active_resources(connection)
        count = _single_row(connection, "SELECT COUNT(*) FROM recording_rules")
        assert count == (1,)
        assert active[0].tracker.tracker_id == replacement_id
        assert active[0].rule.tracker_id == replacement_id


def test_sync_store_delegates_active_resource_query(tmp_path: Path) -> None:
    # Given: one complete linkage persisted through the public store.
    tracker, place, journal, rule = _resource_models()
    with SQLiteStore(tmp_path / "sync-query.db") as store:
        store.upsert_tracker(tracker, NOW)
        store.upsert_place(place, NOW)
        store.upsert_journal(journal, NOW)
        store.upsert_rule(rule, NOW)

        # When: Task 5 asks the store for enabled resources.
        active = store.run_operation(resources.list_active_resources)

        # Then: repository internals remain encapsulated.
        assert active == (
            ConfiguredResources(
                tracker=tracker,
                place=place,
                journal=journal,
                rule=RuleDefinition(
                    rule_id=rule.rule_id,
                    tracker_id=rule.tracker_id,
                    place_id=rule.place_id,
                    journal_id=rule.journal_id,
                    enabled=True,
                    enter_confirmation_seconds=rule.enter_confirmation_seconds,
                    exit_confirmation_seconds=rule.exit_confirmation_seconds,
                    cooldown_seconds=rule.cooldown_seconds,
                    exit_margin_meters=Meters(50),
                    max_gps_accuracy_meters=rule.max_gps_accuracy_meters,
                ),
            ),
        )


@pytest.mark.asyncio
async def test_async_store_offloads_active_resource_query(tmp_path: Path) -> None:
    # Given: one complete linkage persisted through the async store.
    tracker, place, journal, rule = _resource_models()
    store = AsyncSQLiteStore(tmp_path / "async-query.db")
    await store.async_open()
    await store.async_upsert_resources(
        ConfiguredResources(tracker=tracker, place=place, journal=journal, rule=rule),
        NOW,
    )

    # When: Task 5 refreshes listeners without blocking HA's event loop.
    active = await store.async_run_operation(resources.list_active_resources)
    await store.async_close()

    # Then: a typed immutable linkage crosses the worker boundary.
    assert len(active) == 1
    assert active[0].rule.rule_id == RULE_ID
