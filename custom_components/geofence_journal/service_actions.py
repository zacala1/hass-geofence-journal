"""Closed action groups for the administrator service surface."""

from __future__ import annotations

from enum import StrEnum, unique
from typing import Final


@unique
class ServiceAction(StrEnum):
    """Every supported Home Assistant management action."""

    UPSERT_TRACKER = "upsert_tracker"
    UPSERT_PLACE = "upsert_place"
    UPSERT_JOURNAL = "upsert_journal"
    UPSERT_RULE = "upsert_rule"
    LIST_RESOURCES = "list_resources"
    GET_RESOURCE = "get_resource"
    DELETE_RESOURCE = "delete_resource"
    ADD_EVENT = "add_event"
    EXCLUDE_EVENT = "exclude_event"
    RESTORE_EVENT = "restore_event"
    EXPORT_JOURNAL = "export_journal"
    PURGE_EVENTS = "purge_events"
    PURGE_RETENTION = "purge_retention"
    COMPACT_DATABASE = "compact_database"
    RESET_DATABASE = "reset_database"


SERVICE_ACTIONS: Final = tuple(ServiceAction)
RESOURCE_ACTIONS: Final = frozenset(
    {
        ServiceAction.UPSERT_TRACKER,
        ServiceAction.UPSERT_PLACE,
        ServiceAction.UPSERT_JOURNAL,
        ServiceAction.UPSERT_RULE,
    }
)
CATALOG_ACTIONS: Final = frozenset(
    {
        ServiceAction.LIST_RESOURCES,
        ServiceAction.GET_RESOURCE,
        ServiceAction.DELETE_RESOURCE,
    }
)
EVENT_ACTIONS: Final = frozenset(
    {
        ServiceAction.ADD_EVENT,
        ServiceAction.EXCLUDE_EVENT,
        ServiceAction.RESTORE_EVENT,
    }
)
