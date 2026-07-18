"""Privacy-aware CSV export artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, final
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .const import STORAGE_DIRECTORY, STORAGE_INTEGRATION_DIRECTORY
from .storage.records import utc_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.core import HomeAssistant

    from .storage.db_types import SQLConnection, SQLiteValue

EXPORT_LIFETIME: Final = timedelta(hours=24)
DOWNLOAD_PREFIX: Final = "/api/geofence_journal/export"
BASE_HEADERS: Final = (
    "event_id",
    "journal_id",
    "journal_name",
    "rule_id",
    "tracker_id",
    "tracker_name",
    "place_id",
    "place_name",
    "event_type",
    "occurred_at",
    "confirmed_at",
    "source",
    "status",
    "note",
)
COORDINATE_HEADERS: Final = ("latitude", "longitude", "accuracy_m")
EXPORT_DIRECTORY_NAME: Final = "exports"
EXPORT_ID_LENGTH: Final = 32
SPREADSHEET_FORMULA_PREFIXES: Final = ("=", "+", "-", "@")
NAIVE_TIME_ERROR: Final = "naive_export_time"
NAIVE_TIME_MESSAGE: Final = "export time must be aware"
REVERSED_INTERVAL_ERROR: Final = "reversed_export_interval"
REVERSED_INTERVAL_MESSAGE: Final = "export start must not follow end"


def export_directory(hass: HomeAssistant) -> Path:
    """Return the integration-owned export directory below HA storage."""
    return Path(
        hass.config.path(
            STORAGE_DIRECTORY,
            STORAGE_INTEGRATION_DIRECTORY,
            EXPORT_DIRECTORY_NAME,
        )
    )


class ExportClock(Protocol):
    """UTC clock capability injected for deterministic expiry."""

    def utc_now(self) -> datetime:
        """Return the current aware UTC instant."""
        ...


class ExportRequest(BaseModel):
    """Frozen journal/time/privacy filters parsed at the service boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    journal_id: UUID
    start_at: datetime | None = None
    end_at: datetime | None = None
    include_coordinates: bool = False

    @field_validator("start_at", "end_at")
    @classmethod
    def normalize_filter_time(cls, value: datetime | None) -> datetime | None:
        """Normalize aware service times to UTC and reject naive values."""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise PydanticCustomError(NAIVE_TIME_ERROR, NAIVE_TIME_MESSAGE)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def ordered_interval(self) -> ExportRequest:
        """Reject a reversed export interval."""
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.start_at > self.end_at
        ):
            raise PydanticCustomError(
                REVERSED_INTERVAL_ERROR, REVERSED_INTERVAL_MESSAGE
            )
        return self


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    """One opaque, expiring CSV download."""

    export_id: str
    path: Path
    created_at: datetime
    expires_at: datetime
    url: str


@final
class ExportRegistry:
    """Own short-lived export metadata and safe file cleanup."""

    __slots__ = ("_artifacts", "_clock", "_exports_dir")

    def __init__(self, exports_dir: Path, clock: ExportClock) -> None:
        """Bind an integration-owned directory and deterministic UTC clock."""
        self._exports_dir = exports_dir
        self._clock = clock
        self._artifacts: dict[str, ExportArtifact] = {}

    def allocate(self) -> ExportArtifact:
        """Allocate one safe path behind a fresh opaque identifier."""
        export_id = uuid4().hex
        created_at = self._clock.utc_now()
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        artifact = ExportArtifact(
            export_id=export_id,
            path=self._exports_dir / f"{export_id}.csv",
            created_at=created_at,
            expires_at=created_at + EXPORT_LIFETIME,
            url=f"{DOWNLOAD_PREFIX}/{export_id}",
        )
        self._artifacts[export_id] = artifact
        return artifact

    def resolve(self, export_id: str) -> ExportArtifact | None:
        """Resolve one unexpired opaque identifier."""
        if len(export_id) != EXPORT_ID_LENGTH or any(
            character not in "0123456789abcdef" for character in export_id
        ):
            return None
        artifact = self._artifacts.get(export_id)
        if artifact is None:
            return None
        if self._clock.utc_now() >= artifact.expires_at or not artifact.path.is_file():
            self.discard(export_id)
            return None
        return artifact

    def discard(self, export_id: str) -> None:
        """Forget an artifact and remove its file when present."""
        artifact = self._artifacts.pop(export_id, None)
        if artifact is not None:
            artifact.path.unlink(missing_ok=True)

    def cleanup_expired(self) -> int:
        """Remove every registered artifact at or beyond its expiry."""
        expired = tuple(
            export_id
            for export_id, artifact in self._artifacts.items()
            if self._clock.utc_now() >= artifact.expires_at
        )
        for export_id in expired:
            self.discard(export_id)
        return len(expired)

    def discard_all(self) -> None:
        """Invalidate all current URLs and remove every export artifact."""
        for export_id in tuple(self._artifacts):
            self.discard(export_id)
        _removed_orphans = self.cleanup_orphaned_files()

    def cleanup_orphaned_files(self) -> int:
        """Remove export files whose opaque URLs cannot survive a restart."""
        if not self._exports_dir.is_dir():
            return 0
        protected = {
            path
            for artifact in self._artifacts.values()
            for path in (artifact.path, artifact.path.with_suffix(".tmp"))
        }
        orphans = tuple(
            path
            for path in self._exports_dir.iterdir()
            if path not in protected
            and path.suffix in {".csv", ".tmp"}
            and path.is_file()
        )
        for path in orphans:
            path.unlink()
        return len(orphans)


def export_journal_csv(
    connection: SQLConnection, path: Path, request: ExportRequest
) -> int:
    """Write a filtered UTF-8 BOM CSV and return its event count."""
    start_text = None if request.start_at is None else utc_text(request.start_at)
    end_text = None if request.end_at is None else utc_text(request.end_at)
    rows = connection.execute(
        """SELECT e.id,e.journal_id,j.name,e.rule_id,e.tracker_id,t.display_name,
        e.place_id,p.name,e.event_type,e.occurred_at,e.confirmed_at,e.source,
        e.status,e.note,e.latitude,e.longitude,e.accuracy_m
        FROM location_events e
        JOIN journals j ON j.id=e.journal_id
        JOIN trackers t ON t.id=e.tracker_id
        JOIN places p ON p.id=e.place_id
        WHERE e.journal_id=? AND (? IS NULL OR e.occurred_at>=?)
          AND (? IS NULL OR e.occurred_at<=?)
        ORDER BY e.occurred_at,e.id""",
        (
            str(request.journal_id),
            start_text,
            start_text,
            end_text,
            end_text,
        ),
    ).fetchall()
    headers = (
        (*BASE_HEADERS, *COORDINATE_HEADERS)
        if request.include_coordinates
        else BASE_HEADERS
    )
    exported_rows = rows if request.include_coordinates else [row[:14] for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as output:
            _ = output.write(_csv_line(headers))
            for row in exported_rows:
                _ = output.write(_csv_line(row))
        _replaced_path = temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
    return len(rows)


def _csv_line(values: Sequence[SQLiteValue]) -> str:
    return ",".join(_csv_cell(value) for value in values) + "\r\n"


def _csv_cell(value: SQLiteValue) -> str:
    text = "" if value is None else str(value)
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(
        SPREADSHEET_FORMULA_PREFIXES
    ):
        text = f"'{value}"
    if any(character in text for character in (",", '"', "\r", "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text
