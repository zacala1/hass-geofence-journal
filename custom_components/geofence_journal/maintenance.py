"""Frozen request and response contracts for backend management operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, Literal, LiteralString, assert_never
from uuid import UUID

from homeassistant.core import valid_entity_id
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .limits import (
    MAX_CONFIRMATION_SECONDS,
    MAX_COOLDOWN_SECONDS,
    MAX_ENTITY_ID_LENGTH,
    MAX_EXIT_MARGIN_METERS,
    MAX_GPS_ACCURACY_METERS,
    MAX_NAME_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_RADIUS_METERS,
    MAX_REASON_LENGTH,
    MAX_RETENTION_DAYS,
)
from .models import PlaceKind, TrackerKind
from .service_responses import (
    EventResponse,
    ExportResponse,
    JournalEventPayload,
    journal_event_data,
    transition_event_payload,
)

type ServiceUUID = UUID

__all__ = (
    "EventResponse",
    "ExportResponse",
    "JournalEventPayload",
    "journal_event_data",
    "transition_event_payload",
)


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
    entity_id: str = Field(min_length=1, max_length=MAX_ENTITY_ID_LENGTH)
    kind: TrackerKind
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
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
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    source_type: PlaceKind
    zone_entity_id: str | None = Field(default=None, max_length=MAX_ENTITY_ID_LENGTH)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: float | None = Field(default=None, gt=0, le=MAX_RADIUS_METERS)
    exit_margin_meters: float | None = Field(
        default=None, ge=0, le=MAX_EXIT_MARGIN_METERS
    )
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
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    retention_days: int | None = Field(default=None, ge=1, le=MAX_RETENTION_DAYS)
    enabled: bool = True


class UpsertRuleRequest(ServiceRequest):
    """Create or replace one linked recording rule."""

    resource_id: ServiceUUID | None = None
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    tracker_id: ServiceUUID
    place_id: ServiceUUID
    journal_id: ServiceUUID
    enter_confirmation_seconds: int | None = Field(
        default=None, ge=0, le=MAX_CONFIRMATION_SECONDS
    )
    exit_confirmation_seconds: int | None = Field(
        default=None, ge=0, le=MAX_CONFIRMATION_SECONDS
    )
    cooldown_seconds: int | None = Field(default=None, ge=0, le=MAX_COOLDOWN_SECONDS)
    max_gps_accuracy_meters: float = Field(
        default=200, gt=0, le=MAX_GPS_ACCURACY_METERS
    )
    enabled: bool = True


class AddEventRequest(ServiceRequest):
    """Create one manual event from public administrator-supplied fields."""

    journal_id: ServiceUUID
    tracker_id: ServiceUUID
    place_id: ServiceUUID
    occurred_at: datetime
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=MAX_GPS_ACCURACY_METERS)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

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
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)


class RestoreEventRequest(ServiceRequest):
    """Restore one retained event with optional audit context."""

    event_id: ServiceUUID
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)


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


class ResourceResponse(BaseModel):
    """Stable identifier returned by an upsert action."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    resource_id: ServiceUUID
