"""Typed resource construction and persistence for management upserts."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never
from uuid import uuid4

from .maintenance import (
    ResourceResponse,
    UpsertJournalRequest,
    UpsertPlaceRequest,
    UpsertRuleRequest,
    UpsertTrackerRequest,
    service_validation_error,
)
from .models import (
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
    ZonePlace,
)
from .storage.resources import (
    upsert_journal,
    upsert_place,
    upsert_rule,
    upsert_tracker,
)

if TYPE_CHECKING:
    from .export import ExportClock
    from .settings import Settings
    from .storage.async_adapter import AsyncSQLiteStore


async def async_upsert_tracker_resource(
    store: AsyncSQLiteStore, clock: ExportClock, request: UpsertTrackerRequest
) -> ResourceResponse:
    """Build and persist one tracker at a stable or generated identifier."""
    identifier = request.resource_id or uuid4()
    tracker = TrackerDefinition(
        TrackerId(str(identifier)),
        request.entity_id,
        request.kind,
        request.name,
        request.enabled,
    )
    timestamp = clock.utc_now()
    await store.async_run_operation(
        lambda connection: upsert_tracker(connection, tracker, timestamp)
    )
    return ResourceResponse(resource_id=identifier)


async def async_upsert_place_resource(
    store: AsyncSQLiteStore,
    clock: ExportClock,
    settings: Settings,
    request: UpsertPlaceRequest,
) -> ResourceResponse:
    """Build and persist one coordinate or HA Zone place."""
    identifier = request.resource_id or uuid4()
    match request.source_type:
        case PlaceKind.COORDINATE:
            latitude = request.latitude
            longitude = request.longitude
            radius = request.radius_meters
            if latitude is None or longitude is None or radius is None:
                code = "coordinate_place_invariant"
                message = "validated coordinate place is missing geometry"
                raise service_validation_error(code, message)
            place = CoordinatePlace(
                PlaceId(str(identifier)),
                request.name,
                Coordinates(latitude, longitude),
                Meters(radius),
            )
        case PlaceKind.HA_ZONE:
            entity_id = request.zone_entity_id
            if entity_id is None:
                code = "zone_place_invariant"
                message = "validated HA Zone place is missing its entity"
                raise service_validation_error(code, message)
            place = ZonePlace(PlaceId(str(identifier)), request.name, entity_id)
        case unreachable:
            assert_never(unreachable)
    timestamp = clock.utc_now()
    exit_margin = (
        settings.exit_margin_meters
        if request.exit_margin_meters is None
        else Meters(request.exit_margin_meters)
    )
    await store.async_run_operation(
        lambda connection: upsert_place(
            connection,
            place,
            timestamp,
            exit_margin_meters=exit_margin,
            enabled=request.enabled,
        )
    )
    return ResourceResponse(resource_id=identifier)


async def async_upsert_journal_resource(
    store: AsyncSQLiteStore, clock: ExportClock, request: UpsertJournalRequest
) -> ResourceResponse:
    """Build and persist one journal at a stable or generated identifier."""
    identifier = request.resource_id or uuid4()
    journal = JournalDefinition(
        JournalId(str(identifier)), request.name, request.enabled
    )
    timestamp = clock.utc_now()
    await store.async_run_operation(
        lambda connection: upsert_journal(connection, journal, timestamp)
    )
    return ResourceResponse(resource_id=identifier)


async def async_upsert_rule_resource(
    store: AsyncSQLiteStore,
    clock: ExportClock,
    settings: Settings,
    request: UpsertRuleRequest,
) -> ResourceResponse:
    """Build and persist one rule after SQLite validates all references."""
    identifier = request.resource_id or uuid4()
    rule = RuleDefinition(
        rule_id=RuleId(str(identifier)),
        tracker_id=TrackerId(str(request.tracker_id)),
        place_id=PlaceId(str(request.place_id)),
        journal_id=JournalId(str(request.journal_id)),
        enabled=request.enabled,
        enter_confirmation_seconds=Seconds(
            settings.enter_confirmation_seconds
            if request.enter_confirmation_seconds is None
            else request.enter_confirmation_seconds
        ),
        exit_confirmation_seconds=Seconds(
            settings.exit_confirmation_seconds
            if request.exit_confirmation_seconds is None
            else request.exit_confirmation_seconds
        ),
        cooldown_seconds=Seconds(
            settings.cooldown_seconds
            if request.cooldown_seconds is None
            else request.cooldown_seconds
        ),
        exit_margin_meters=Meters(0),
        max_gps_accuracy_meters=Meters(request.max_gps_accuracy_meters),
    )
    timestamp = clock.utc_now()
    await store.async_run_operation(
        lambda connection: upsert_rule(connection, rule, timestamp, name=request.name)
    )
    return ResourceResponse(resource_id=identifier)
