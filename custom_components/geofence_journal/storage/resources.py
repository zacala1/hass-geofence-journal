"""Typed writes for configured trackers, places, journals, and rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from datetime import datetime

    from .db_types import SQLConnection

from custom_components.geofence_journal.models import (
    CoordinatePlace,
    JournalDefinition,
    RuleDefinition,
    TrackerDefinition,
    ZonePlace,
)

from .records import utc_text


@dataclass(frozen=True, slots=True)
class ConfiguredResources:
    """One complete runnable tracker/place/journal/rule linkage."""

    tracker: TrackerDefinition
    place: CoordinatePlace | ZonePlace
    journal: JournalDefinition
    rule: RuleDefinition


def upsert_tracker(
    connection: SQLConnection,
    tracker: TrackerDefinition,
    timestamp: datetime,
) -> None:
    """Create or replace a tracker definition."""
    stamp = utc_text(timestamp)
    _ = connection.execute(
        """INSERT INTO trackers
        (id,entity_id,display_name,tracker_kind,enabled,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        entity_id=excluded.entity_id,display_name=excluded.display_name,
        tracker_kind=excluded.tracker_kind,enabled=excluded.enabled,
        updated_at=excluded.updated_at""",
        (
            tracker.tracker_id,
            tracker.entity_id,
            tracker.name,
            tracker.kind.value,
            int(tracker.enabled),
            stamp,
            stamp,
        ),
    )


def upsert_place(
    connection: SQLConnection,
    place: CoordinatePlace | ZonePlace,
    timestamp: datetime,
) -> None:
    """Create or replace either supported place definition."""
    stamp = utc_text(timestamp)
    match place:
        case CoordinatePlace():
            parameters = (
                place.place_id,
                place.name,
                "coordinates",
                None,
                place.center.latitude,
                place.center.longitude,
                place.radius_m,
                stamp,
                stamp,
            )
        case ZonePlace():
            parameters = (
                place.place_id,
                place.name,
                "ha_zone",
                place.entity_id,
                None,
                None,
                None,
                stamp,
                stamp,
            )
        case unreachable:
            assert_never(unreachable)
    _ = connection.execute(
        """INSERT INTO places
        (id,name,source_type,zone_entity_id,latitude,longitude,radius_m,
         created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,
        source_type=excluded.source_type,zone_entity_id=excluded.zone_entity_id,
        latitude=excluded.latitude,longitude=excluded.longitude,
        radius_m=excluded.radius_m,updated_at=excluded.updated_at""",
        parameters,
    )


def upsert_journal(
    connection: SQLConnection,
    journal: JournalDefinition,
    timestamp: datetime,
) -> None:
    """Create or replace a journal definition."""
    stamp = utc_text(timestamp)
    _ = connection.execute(
        """INSERT INTO journals (id,name,enabled,created_at,updated_at)
        VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        name=excluded.name,enabled=excluded.enabled,updated_at=excluded.updated_at""",
        (journal.journal_id, journal.name, int(journal.enabled), stamp, stamp),
    )


def upsert_rule(
    connection: SQLConnection,
    rule: RuleDefinition,
    timestamp: datetime,
) -> None:
    """Create or replace a linked recording rule."""
    stamp = utc_text(timestamp)
    _ = connection.execute(
        """INSERT INTO recording_rules
        (id,name,tracker_id,place_id,journal_id,enter_confirmation_seconds,
         exit_confirmation_seconds,cooldown_seconds,max_accuracy_m,enabled,
         created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET tracker_id=excluded.tracker_id,
        place_id=excluded.place_id,journal_id=excluded.journal_id,
        enter_confirmation_seconds=excluded.enter_confirmation_seconds,
        exit_confirmation_seconds=excluded.exit_confirmation_seconds,
        cooldown_seconds=excluded.cooldown_seconds,
        max_accuracy_m=excluded.max_accuracy_m,enabled=excluded.enabled,
        updated_at=excluded.updated_at""",
        (
            rule.rule_id,
            str(rule.rule_id),
            rule.tracker_id,
            rule.place_id,
            rule.journal_id,
            rule.enter_confirmation_seconds,
            rule.exit_confirmation_seconds,
            rule.cooldown_seconds,
            rule.max_gps_accuracy_meters,
            int(rule.enabled),
            stamp,
            stamp,
        ),
    )
