"""Frozen request and response contracts for backend management operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Literal, LiteralString, assert_never
from uuid import UUID

from homeassistant.core import valid_entity_id
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .models import EventStatus, PlaceKind, TrackerKind
from .runtime.state import RuntimeInvariantError
from .storage.records import utc_text

if TYPE_CHECKING:
    from homeassistant.util.json import JsonObjectType

    from .storage.records import RuntimeStateRecord, TransitionResult
    from .storage.resources import ConfiguredResources

type ServiceUUID = UUID


def service_validation_error(
    code: LiteralString, message: LiteralString
) -> PydanticCustomError:
    """Build one typed Pydantic boundary error without raising it here."""
    return PydanticCustomError(code, message)


class ServiceRequest(BaseModel):
    """Frozen base model parsed once from Home Assistant service data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class UpsertTrackerRequest(ServiceRequest):
    """Create or replace one supported Home Assistant tracker."""

    resource_id: ServiceUUID | None = None
    entity_id: str = Field(min_length=1)
    kind: TrackerKind
    name: str = Field(min_length=1)
    enabled: bool = True

    @model_validator(mode="after")
    def entity_domain_matches_kind(self) -> UpsertTrackerRequest:
        """Reject cross-domain tracker definitions."""
        domain = self.entity_id.partition(".")[0]
        if domain != self.kind.value or not valid_entity_id(self.entity_id):
            code = "tracker_entity_domain"
            message = "tracker entity domain does not match tracker kind"
            raise service_validation_error(code, message)
        return self


class UpsertPlaceRequest(ServiceRequest):
    """Create or replace one coordinate or Home Assistant Zone place."""

    resource_id: ServiceUUID | None = None
    name: str = Field(min_length=1)
    source_type: PlaceKind
    zone_entity_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: float | None = Field(default=None, gt=0)
    exit_margin_meters: float | None = Field(default=None, ge=0)
    enabled: bool = True

    @model_validator(mode="after")
    def source_fields_match_kind(self) -> UpsertPlaceRequest:
        """Require exactly the fields owned by the selected place kind."""
        match self.source_type:
            case PlaceKind.COORDINATE:
                if (
                    self.zone_entity_id is not None
                    or self.latitude is None
                    or self.longitude is None
                    or self.radius_meters is None
                ):
                    code = "coordinate_place_shape"
                    message = "coordinate place requires only coordinate fields"
                    raise service_validation_error(code, message)
            case PlaceKind.HA_ZONE:
                if (
                    self.zone_entity_id is None
                    or not valid_entity_id(self.zone_entity_id)
                    or self.zone_entity_id.partition(".")[0] != "zone"
                    or self.latitude is not None
                    or self.longitude is not None
                    or self.radius_meters is not None
                ):
                    code = "zone_place_shape"
                    message = "HA Zone place requires only a zone entity"
                    raise service_validation_error(code, message)
            case unreachable:
                assert_never(unreachable)
        return self


class UpsertJournalRequest(ServiceRequest):
    """Create or replace one named event journal."""

    resource_id: ServiceUUID | None = None
    name: str = Field(min_length=1)
    enabled: bool = True


class UpsertRuleRequest(ServiceRequest):
    """Create or replace one linked recording rule."""

    resource_id: ServiceUUID | None = None
    name: str = Field(min_length=1)
    tracker_id: ServiceUUID
    place_id: ServiceUUID
    journal_id: ServiceUUID
    enter_confirmation_seconds: int | None = Field(default=None, ge=0)
    exit_confirmation_seconds: int | None = Field(default=None, ge=0)
    cooldown_seconds: int | None = Field(default=None, ge=0)
    max_gps_accuracy_meters: float = Field(default=200, gt=0)
    enabled: bool = True


class AddEventRequest(ServiceRequest):
    """Create one manual event from public administrator-supplied fields."""

    journal_id: ServiceUUID
    tracker_id: ServiceUUID
    place_id: ServiceUUID
    occurred_at: datetime
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    note: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        """Require an aware instant and normalize it to UTC."""
        if value.tzinfo is None or value.utcoffset() is None:
            code = "naive_event_time"
            message = "event time must be timezone-aware"
            raise service_validation_error(code, message)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def coordinates_are_complete(self) -> AddEventRequest:
        """Require a complete optional coordinate tuple."""
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude or (
            self.accuracy_m is not None and not has_latitude
        ):
            code = "manual_coordinate_shape"
            message = (
                "latitude and longitude are required together; accuracy needs both"
            )
            raise service_validation_error(code, message)
        return self


class ExcludeEventRequest(ServiceRequest):
    """Exclude one retained event with optional audit context."""

    event_id: ServiceUUID
    reason: str | None = None


class RestoreEventRequest(ServiceRequest):
    """Restore one retained event with optional audit context."""

    event_id: ServiceUUID
    reason: str | None = None


class PurgeEventsRequest(ServiceRequest):
    """Select old events and control whether deletion is only simulated."""

    before: datetime
    journal_id: ServiceUUID
    dry_run: bool = True
    confirm: bool = False

    @field_validator("before")
    @classmethod
    def normalize_before(cls, value: datetime) -> datetime:
        """Normalize an aware cutoff to UTC."""
        if value.tzinfo is None or value.utcoffset() is None:
            code = "naive_purge_time"
            message = "purge cutoff must be timezone-aware"
            raise service_validation_error(code, message)
        return value.astimezone(UTC)


class ResetDatabaseRequest(ServiceRequest):
    """Require the exact destructive reset confirmation phrase."""

    confirmation: Literal["DELETE ALL GEOFENCE JOURNAL DATA"]


@dataclass(frozen=True, slots=True)
class JournalEventPayload:
    """Coordinate-free public payload for one journal event change."""

    event_id: str
    journal_id: str
    journal_name: str
    rule_id: str | None
    tracker_id: str
    tracker_name: str
    place_id: str
    place_name: str
    event_type: str
    status: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class EventResponse:
    """One event mutation result and its coordinate-free bus payload."""

    changed: bool
    payload: JournalEventPayload


@dataclass(frozen=True, slots=True)
class ExportResponse:
    """Opaque authenticated export location and expiry metadata."""

    url: str
    expires_at: str
    count: int


def journal_event_data(payload: JournalEventPayload) -> JsonObjectType:
    """Convert one immutable payload to Home Assistant's dictionary shape."""
    return {
        "event_id": payload.event_id,
        "journal_id": payload.journal_id,
        "journal_name": payload.journal_name,
        "rule_id": payload.rule_id,
        "tracker_id": payload.tracker_id,
        "tracker_name": payload.tracker_name,
        "place_id": payload.place_id,
        "place_name": payload.place_name,
        "event_type": payload.event_type,
        "status": payload.status,
        "timestamp": payload.timestamp,
    }


def transition_event_payload(
    result: TransitionResult,
    state: RuntimeStateRecord,
    resources: ConfiguredResources,
) -> JournalEventPayload:
    """Build the shared payload for one newly committed runtime transition."""
    event_type = state.last_event_type
    timestamp = state.last_event_at
    if event_type is None or timestamp is None:
        raise RuntimeInvariantError(
            detail="committed transition is missing event metadata"
        )
    return JournalEventPayload(
        event_id=result.event_id,
        journal_id=str(resources.journal.journal_id),
        journal_name=resources.journal.name,
        rule_id=str(resources.rule.rule_id),
        tracker_id=str(resources.tracker.tracker_id),
        tracker_name=resources.tracker.name,
        place_id=str(resources.place.place_id),
        place_name=resources.place.name,
        event_type=event_type.value,
        status=EventStatus.CONFIRMED.value,
        timestamp=utc_text(timestamp),
    )


class ResourceResponse(BaseModel):
    """Stable identifier returned by an upsert action."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    resource_id: ServiceUUID
