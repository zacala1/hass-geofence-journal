"""Typed SQLite reads and atomic deletes for configured resources."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, assert_never

from custom_components.geofence_journal.models import PlaceKind, TrackerKind
from custom_components.geofence_journal.resource_catalog import (
    JournalResourceItem,
    PlaceResourceItem,
    ResourceCatalogError,
    ResourceCatalogItem,
    ResourceDeleteResponse,
    ResourceInUseError,
    ResourceNotFoundError,
    ResourceType,
    RuleResourceItem,
    TrackerResourceItem,
)

from .db_types import SQLiteRow, SQLiteValue, required_integer, required_text
from .errors import DatabaseSchemaError

if TYPE_CHECKING:
    from .db_types import SQLConnection


def list_resources(
    connection: SQLConnection,
    resource_type: ResourceType | None,
    *,
    include_disabled: bool = True,
) -> tuple[ResourceCatalogItem, ...]:
    """List one or every resource type in deterministic order."""
    selected = tuple(ResourceType) if resource_type is None else (resource_type,)
    items = [
        item
        for selected_type in selected
        for item in _list_type(
            connection, selected_type, include_disabled=include_disabled
        )
    ]
    return tuple(
        sorted(items, key=lambda item: (item.resource_type.value, item.resource_id))
    )


def get_resource(
    connection: SQLConnection,
    resource_type: ResourceType,
    resource_id: str,
) -> ResourceCatalogItem:
    """Return one exact configured resource or a stable not-found error."""
    items = _list_type(
        connection,
        resource_type,
        include_disabled=True,
        resource_id=resource_id,
    )
    if not items:
        raise ResourceNotFoundError(resource_type, resource_id)
    return items[0]


def delete_resource(
    connection: SQLConnection,
    resource_type: ResourceType,
    resource_id: str,
) -> ResourceDeleteResponse:
    """Atomically delete one unreferenced resource and associated runtime state."""
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        _ = get_resource(connection, resource_type, resource_id)
        if resource_type is ResourceType.RULE:
            _ = connection.execute(
                "DELETE FROM runtime_states WHERE rule_id=?", (resource_id,)
            )
        _delete_row(connection, resource_type, resource_id)
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise ResourceInUseError(resource_type, resource_id) from error
    except sqlite3.Error, ResourceCatalogError:
        connection.rollback()
        raise
    connection.commit()
    return ResourceDeleteResponse(resource_type, resource_id)


def _list_type(
    connection: SQLConnection,
    resource_type: ResourceType,
    *,
    include_disabled: bool,
    resource_id: str | None = None,
) -> tuple[ResourceCatalogItem, ...]:
    parameters = (int(include_disabled), resource_id, resource_id)
    match resource_type:
        case ResourceType.TRACKER:
            rows = connection.execute(
                """SELECT id,display_name,enabled,entity_id,tracker_kind
                FROM trackers WHERE (?=1 OR enabled=1) AND (? IS NULL OR id=?)
                ORDER BY id""",
                parameters,
            )
            rows = rows.fetchall()
            return tuple(_tracker(row) for row in rows)
        case ResourceType.PLACE:
            rows = connection.execute(
                """SELECT id,name,enabled,source_type,zone_entity_id,latitude,
                longitude,radius_m,exit_margin_m FROM places
                WHERE (?=1 OR enabled=1) AND (? IS NULL OR id=?) ORDER BY id""",
                parameters,
            )
            rows = rows.fetchall()
            return tuple(_place(row) for row in rows)
        case ResourceType.JOURNAL:
            rows = connection.execute(
                """SELECT id,name,enabled,view_type,retention_days FROM journals
                WHERE (?=1 OR enabled=1) AND (? IS NULL OR id=?) ORDER BY id""",
                parameters,
            )
            rows = rows.fetchall()
            return tuple(_journal(row) for row in rows)
        case ResourceType.RULE:
            rows = connection.execute(
                """SELECT id,name,enabled,tracker_id,place_id,journal_id,
                record_enter,record_exit,record_stay,enter_confirmation_seconds,
                exit_confirmation_seconds,cooldown_seconds,max_accuracy_m
                FROM recording_rules WHERE (?=1 OR enabled=1)
                AND (? IS NULL OR id=?) ORDER BY id""",
                parameters,
            )
            rows = rows.fetchall()
            return tuple(_rule(row) for row in rows)
        case unreachable:
            assert_never(unreachable)


def _tracker(row: SQLiteRow) -> TrackerResourceItem:
    return TrackerResourceItem(
        resource_id=required_text(row[0], field="tracker id"),
        name=required_text(row[1], field="tracker name"),
        enabled=_bool(row[2], field="tracker enabled"),
        entity_id=required_text(row[3], field="tracker entity"),
        kind=TrackerKind(required_text(row[4], field="tracker kind")),
    )


def _place(row: SQLiteRow) -> PlaceResourceItem:
    return PlaceResourceItem(
        resource_id=required_text(row[0], field="place id"),
        name=required_text(row[1], field="place name"),
        enabled=_bool(row[2], field="place enabled"),
        source_type=PlaceKind(required_text(row[3], field="place source")),
        zone_entity_id=_optional_text(row[4], field="place zone"),
        latitude=_optional_float(row[5], field="place latitude"),
        longitude=_optional_float(row[6], field="place longitude"),
        radius_meters=_optional_float(row[7], field="place radius"),
        exit_margin_meters=_float(row[8], field="place exit margin"),
    )


def _journal(row: SQLiteRow) -> JournalResourceItem:
    return JournalResourceItem(
        resource_id=required_text(row[0], field="journal id"),
        name=required_text(row[1], field="journal name"),
        enabled=_bool(row[2], field="journal enabled"),
        view_type=required_text(row[3], field="journal view type"),
        retention_days=_optional_int(row[4], field="journal retention"),
    )


def _rule(row: SQLiteRow) -> RuleResourceItem:
    return RuleResourceItem(
        resource_id=required_text(row[0], field="rule id"),
        name=required_text(row[1], field="rule name"),
        enabled=_bool(row[2], field="rule enabled"),
        tracker_id=required_text(row[3], field="rule tracker"),
        place_id=required_text(row[4], field="rule place"),
        journal_id=required_text(row[5], field="rule journal"),
        record_enter=_bool(row[6], field="rule record enter"),
        record_exit=_bool(row[7], field="rule record exit"),
        record_stay=_bool(row[8], field="rule record stay"),
        enter_confirmation_seconds=required_integer(
            row[9], field="rule enter confirmation"
        ),
        exit_confirmation_seconds=required_integer(
            row[10], field="rule exit confirmation"
        ),
        cooldown_seconds=required_integer(row[11], field="rule cooldown"),
        max_gps_accuracy_meters=_optional_float(row[12], field="rule accuracy"),
    )


def _delete_row(
    connection: SQLConnection, resource_type: ResourceType, resource_id: str
) -> None:
    match resource_type:
        case ResourceType.TRACKER:
            sql = "DELETE FROM trackers WHERE id=?"
        case ResourceType.PLACE:
            sql = "DELETE FROM places WHERE id=?"
        case ResourceType.JOURNAL:
            sql = "DELETE FROM journals WHERE id=?"
        case ResourceType.RULE:
            sql = "DELETE FROM recording_rules WHERE id=?"
        case unreachable:
            assert_never(unreachable)
    _ = connection.execute(sql, (resource_id,))


def _bool(value: SQLiteValue, *, field: str) -> bool:
    parsed = required_integer(value, field=field)
    if parsed not in {0, 1}:
        raise DatabaseSchemaError(detail=f"{field} must be zero or one")
    return bool(parsed)


def _float(value: SQLiteValue, *, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise DatabaseSchemaError(detail=f"{field} must be REAL")


def _optional_float(value: SQLiteValue, *, field: str) -> float | None:
    return None if value is None else _float(value, field=field)


def _optional_text(value: SQLiteValue, *, field: str) -> str | None:
    return None if value is None else required_text(value, field=field)


def _optional_int(value: SQLiteValue, *, field: str) -> int | None:
    return None if value is None else required_integer(value, field=field)
