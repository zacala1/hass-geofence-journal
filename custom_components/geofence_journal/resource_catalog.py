"""Typed public contracts for resource discovery and deletion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import ClassVar, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import BaseModel, ConfigDict

from .maintenance import ServiceRequest
from .models import PlaceKind, TrackerKind  # noqa: TC001 - Pydantic runtime types.


@unique
class ResourceType(StrEnum):
    """Configured resource tables available through management services."""

    TRACKER = "tracker"
    PLACE = "place"
    JOURNAL = "journal"
    RULE = "rule"


class ListResourcesRequest(ServiceRequest):
    """Select one resource type or the complete configuration catalog."""

    resource_type: ResourceType | None = None
    include_disabled: bool = True


class GetResourceRequest(ServiceRequest):
    """Select one configured resource by stable identifier."""

    resource_type: ResourceType
    resource_id: UUID


class DeleteResourceRequest(GetResourceRequest):
    """Require explicit confirmation before deleting configuration."""

    confirm: Literal[True]


class ResourceItem(BaseModel):
    """Shared immutable resource fields returned to administrators."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    resource_id: str
    name: str
    enabled: bool


class TrackerResourceItem(ResourceItem):
    """One configured tracker."""

    resource_type: Literal[ResourceType.TRACKER] = ResourceType.TRACKER
    entity_id: str
    kind: TrackerKind


class PlaceResourceItem(ResourceItem):
    """One configured fixed or Home Assistant zone place."""

    resource_type: Literal[ResourceType.PLACE] = ResourceType.PLACE
    source_type: PlaceKind
    zone_entity_id: str | None
    latitude: float | None
    longitude: float | None
    radius_meters: float | None
    exit_margin_meters: float


class JournalResourceItem(ResourceItem):
    """One configured journal."""

    resource_type: Literal[ResourceType.JOURNAL] = ResourceType.JOURNAL
    view_type: str
    retention_days: int | None


class RuleResourceItem(ResourceItem):
    """One configured tracker/place/journal recording rule."""

    resource_type: Literal[ResourceType.RULE] = ResourceType.RULE
    tracker_id: str
    place_id: str
    journal_id: str
    record_enter: bool
    record_exit: bool
    record_stay: bool
    enter_confirmation_seconds: int
    exit_confirmation_seconds: int
    cooldown_seconds: int
    max_gps_accuracy_meters: float | None


type ResourceCatalogItem = (
    TrackerResourceItem | PlaceResourceItem | JournalResourceItem | RuleResourceItem
)


@dataclass(frozen=True, slots=True)
class ResourceListResponse:
    """Deterministically ordered resource catalog response."""

    resources: tuple[ResourceCatalogItem, ...]


@dataclass(frozen=True, slots=True)
class ResourceGetResponse:
    """One exact resource response."""

    resource: ResourceCatalogItem


@dataclass(frozen=True, slots=True)
class ResourceDeleteResponse:
    """Identity of one successfully deleted resource."""

    resource_type: ResourceType
    resource_id: str


class ResourceCatalogError(ValueError):
    """Base class for stable resource management failures."""


class ResourceNotFoundError(ResourceCatalogError):
    """Requested resource identity does not exist."""

    def __init__(self, resource_type: ResourceType, resource_id: str) -> None:
        """Describe the missing typed identity without private input data."""
        super().__init__(f"{resource_type.value} resource not found: {resource_id}")


class ResourceInUseError(ResourceCatalogError):
    """A foreign-key reference protects the requested resource."""

    def __init__(self, resource_type: ResourceType, resource_id: str) -> None:
        """Describe the protected identity and leave its references untouched."""
        super().__init__(
            f"{resource_type.value} resource is still in use: {resource_id}"
        )
