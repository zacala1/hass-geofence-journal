"""Typed read model for enabled resource linkages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    PlaceKind,
    RuleDefinition,
    RuleId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
    ZonePlace,
)

from .db_types import SQLiteRow, SQLiteValue, required_integer, required_text
from .errors import DatabaseSchemaError

if TYPE_CHECKING:
    from .db_types import SQLConnection


@dataclass(frozen=True, slots=True)
class ConfiguredResources:
    """One complete runnable tracker/place/journal/rule linkage."""

    tracker: TrackerDefinition
    place: CoordinatePlace | ZonePlace
    journal: JournalDefinition
    rule: RuleDefinition


def list_active_resources(
    connection: SQLConnection,
) -> tuple[ConfiguredResources, ...]:
    """Load enabled rule linkages for runtime/listener refresh."""
    rows = connection.execute(
        """SELECT t.id,t.entity_id,t.display_name,t.tracker_kind,
        p.id,p.name,p.source_type,p.zone_entity_id,p.latitude,p.longitude,p.radius_m,
        p.exit_margin_m,j.id,j.name,j.retention_days,r.id,r.enter_confirmation_seconds,
        r.exit_confirmation_seconds,r.cooldown_seconds,r.max_accuracy_m
        FROM recording_rules r
        JOIN trackers t ON t.id=r.tracker_id
        JOIN places p ON p.id=r.place_id
        JOIN journals j ON j.id=r.journal_id
        WHERE r.enabled=1 AND t.enabled=1 AND p.enabled=1 AND j.enabled=1
        ORDER BY r.id"""
    ).fetchall()
    return tuple(_configured_resource(row) for row in rows)


def _configured_resource(row: SQLiteRow) -> ConfiguredResources:
    tracker_id = TrackerId(required_text(row[0], field="trackers.id"))
    place_id = PlaceId(required_text(row[4], field="places.id"))
    journal_id = JournalId(required_text(row[12], field="journals.id"))
    source_type = PlaceKind(required_text(row[6], field="places.source_type"))
    match source_type:
        case PlaceKind.COORDINATE:
            place = CoordinatePlace(
                place_id=place_id,
                name=required_text(row[5], field="places.name"),
                center=Coordinates(
                    latitude=_required_float(row[8], field="places.latitude"),
                    longitude=_required_float(row[9], field="places.longitude"),
                ),
                radius_m=Meters(_required_float(row[10], field="places.radius_m")),
            )
        case PlaceKind.HA_ZONE:
            place = ZonePlace(
                place_id=place_id,
                name=required_text(row[5], field="places.name"),
                entity_id=required_text(row[7], field="places.zone_entity_id"),
            )
        case unreachable:
            assert_never(unreachable)
    return ConfiguredResources(
        tracker=TrackerDefinition(
            tracker_id=tracker_id,
            entity_id=required_text(row[1], field="trackers.entity_id"),
            name=required_text(row[2], field="trackers.display_name"),
            kind=TrackerKind(required_text(row[3], field="trackers.tracker_kind")),
            enabled=True,
        ),
        place=place,
        journal=JournalDefinition(
            journal_id=journal_id,
            name=required_text(row[13], field="journals.name"),
            enabled=True,
            retention_days=(
                None
                if row[14] is None
                else required_integer(row[14], field="journals.retention_days")
            ),
        ),
        rule=RuleDefinition(
            rule_id=RuleId(required_text(row[15], field="recording_rules.id")),
            tracker_id=tracker_id,
            place_id=place_id,
            journal_id=journal_id,
            enabled=True,
            enter_confirmation_seconds=Seconds(
                required_integer(row[16], field="enter_confirmation")
            ),
            exit_confirmation_seconds=Seconds(
                required_integer(row[17], field="exit_confirmation")
            ),
            cooldown_seconds=Seconds(required_integer(row[18], field="cooldown")),
            exit_margin_meters=Meters(
                _required_float(row[11], field="places.exit_margin_m")
            ),
            max_gps_accuracy_meters=Meters(
                _required_float(row[19], field="max_accuracy_m")
            ),
        ),
    )


def _required_float(value: SQLiteValue, *, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise DatabaseSchemaError(detail=f"{field} must be REAL")
