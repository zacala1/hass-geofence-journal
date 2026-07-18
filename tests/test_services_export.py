import csv
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from io import StringIO
from typing import TYPE_CHECKING, Final, final
from uuid import UUID

import pytest
from custom_components.geofence_journal import export
from custom_components.geofence_journal.export import (
    ExportRegistry,
    ExportRequest,
    export_journal_csv,
)
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    EventId,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.storage.events import AddEventRequest, add_event
from custom_components.geofence_journal.storage.resources import (
    upsert_journal,
    upsert_place,
    upsert_tracker,
)
from custom_components.geofence_journal.storage.schema import bootstrap_v1
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")
OTHER_JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000004")


def test_export_module_exists() -> None:
    # Given: CSV is the sole v0.1.0 reporting surface.
    module_name = "custom_components.geofence_journal.export"

    # When: the export module is discovered.
    export_spec = find_spec(module_name)

    # Then: Task 5 can inject it without a frontend dependency.
    assert export_spec is not None


def test_export_module_exposes_frozen_artifact_registry_contract() -> None:
    # Given: service and HTTP layers share only opaque export artifacts.
    required_names = {
        "ExportRequest",
        "ExportArtifact",
        "ExportRegistry",
        "export_directory",
        "export_journal_csv",
    }

    # When: the module contract is inspected.
    exposed_names = set(dir(export))

    # Then: no query websocket or frontend-facing CRUD leaks into the surface.
    assert required_names <= exposed_names


def test_export_directory_is_inside_the_integration_storage_root(
    hass: HomeAssistant,
) -> None:
    # Given: Home Assistant's active config directory.

    # When: Task 5 requests the only permitted export root.
    export_root = export.export_directory(hass)

    # Then: files live exactly below .storage/geofence_journal/exports.
    assert export_root.parts[-3:] == (".storage", "geofence_journal", "exports")


@final
class FakeExportClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _seed_export_database(connection: sqlite3.Connection) -> None:
    bootstrap_v1(connection)
    _ = connection.execute("PRAGMA foreign_keys=ON")
    upsert_tracker(
        connection,
        TrackerDefinition(
            tracker_id=TRACKER_ID,
            entity_id="person.alice",
            kind=TrackerKind.PERSON,
            name="Alice",
            enabled=True,
        ),
        NOW,
    )
    upsert_place(
        connection,
        CoordinatePlace(
            place_id=PLACE_ID,
            name="Home",
            center=Coordinates(latitude=37.5, longitude=127.0),
            radius_m=Meters(100),
        ),
        NOW,
    )
    upsert_journal(
        connection,
        JournalDefinition(journal_id=JOURNAL_ID, name="Presence", enabled=True),
        NOW,
    )
    upsert_journal(
        connection,
        JournalDefinition(journal_id=OTHER_JOURNAL_ID, name="Other", enabled=True),
        NOW,
    )
    for event_id, journal_id, occurred_at, note in (
        ("event-before", JOURNAL_ID, NOW - timedelta(hours=2), "before"),
        ("event-match", JOURNAL_ID, NOW, '도착, "확인"'),
        ("event-other", OTHER_JOURNAL_ID, NOW, "other"),
    ):
        _ = add_event(
            connection,
            AddEventRequest(
                event_id=EventId(event_id),
                journal_id=journal_id,
                tracker_id=TRACKER_ID,
                place_id=PLACE_ID,
                occurred_at=occurred_at,
                confirmed_at=occurred_at,
                latitude=37.5,
                longitude=127.0,
                accuracy_m=5,
                note=note,
            ),
        )


def test_csv_export_filters_time_and_omits_coordinates_by_default(
    tmp_path: Path,
) -> None:
    # Given: two journals, one event outside the selected time window, and GPS data.
    output = tmp_path / "filtered.csv"
    with closing(
        sqlite3.connect(tmp_path / "export.db", isolation_level=None)
    ) as connection:
        _seed_export_database(connection)

        # When: an admin exports one journal without privacy opt-in.
        count = export_journal_csv(
            connection,
            output,
            ExportRequest(
                journal_id=UUID(str(JOURNAL_ID)),
                start_at=NOW - timedelta(minutes=1),
                end_at=NOW + timedelta(minutes=1),
            ),
        )

    # Then: UTF-8 BOM, filters, Korean text, and coordinate omission are exact.
    raw = output.read_bytes()
    rows = list(csv.DictReader(StringIO(raw.decode("utf-8-sig"))))
    assert raw.startswith(b"\xef\xbb\xbf")
    assert count == 1
    assert [row["event_id"] for row in rows] == ["event-match"]
    assert rows[0]["note"] == '도착, "확인"'
    assert "latitude" not in rows[0]
    assert "longitude" not in rows[0]
    assert "accuracy_m" not in rows[0]


def test_csv_export_includes_only_stored_coordinates_when_requested(
    tmp_path: Path,
) -> None:
    # Given: a selected event with stored coordinates.
    output = tmp_path / "coordinates.csv"
    with closing(
        sqlite3.connect(tmp_path / "coordinates.db", isolation_level=None)
    ) as connection:
        _seed_export_database(connection)

        # When: coordinates are explicitly requested.
        count = export_journal_csv(
            connection,
            output,
            ExportRequest(journal_id=UUID(str(JOURNAL_ID)), include_coordinates=True),
        )

    # Then: stored values appear and no synthetic value is introduced.
    rows = list(csv.DictReader(StringIO(output.read_text("utf-8-sig"))))
    assert count == 2
    assert rows[0]["latitude"] == "37.5"
    assert rows[0]["longitude"] == "127.0"
    assert rows[0]["accuracy_m"] == "5.0"


@pytest.mark.parametrize("formula", ["=1+1", "+1+1", "-1+1", "@SUM(A1)", " \t=1+1"])
def test_csv_export_neutralizes_spreadsheet_formulas(
    tmp_path: Path, formula: str
) -> None:
    # Given: an administrator-authored note that a spreadsheet could execute.
    output = tmp_path / "formula.csv"
    with closing(sqlite3.connect(tmp_path / "formula.db", isolation_level=None)) as db:
        _seed_export_database(db)
        _ = db.execute(
            "UPDATE location_events SET note=? WHERE id='event-match'", (formula,)
        )

        # When: the journal is exported for spreadsheet use.
        _ = export_journal_csv(
            db,
            output,
            ExportRequest(journal_id=UUID(str(JOURNAL_ID)), start_at=NOW),
        )

    # Then: a leading apostrophe forces the cell to remain inert text.
    rows = list(csv.DictReader(StringIO(output.read_text("utf-8-sig"))))
    assert rows[0]["note"] == f"'{formula}"


def test_export_request_rejects_naive_or_reversed_time_filters() -> None:
    # Given: filters that cannot define one UTC interval.
    invalid_filters = (
        {"start_at": NOW.replace(tzinfo=None)},
        {"start_at": NOW, "end_at": NOW - timedelta(seconds=1)},
    )

    # When / Then: each is rejected once at the service boundary.
    for filters in invalid_filters:
        with pytest.raises(ValidationError):
            _ = ExportRequest.model_validate({"journal_id": JOURNAL_ID, **filters})
    request = ExportRequest(journal_id=UUID(str(JOURNAL_ID)), start_at=None)
    assert request.start_at is None


def test_export_registry_rejects_traversal_and_deletes_at_expiry(
    tmp_path: Path,
) -> None:
    # Given: one allocated artifact with a deterministic 24-hour lifetime.
    clock = FakeExportClock(NOW)
    registry = ExportRegistry(tmp_path / "exports", clock)
    artifact = registry.allocate()
    _ = artifact.path.write_bytes(b"csv")

    # When: an attacker probes traversal and time reaches exact expiry.
    traversal = registry.resolve("../../geofence_journal.db")
    clock.advance(timedelta(hours=24))
    expired = registry.resolve(artifact.export_id)

    # Then: neither ID resolves and the expired file is removed.
    assert traversal is None
    assert expired is None
    assert not artifact.path.exists()


def test_export_registry_discard_all_invalidates_every_current_url(
    tmp_path: Path,
) -> None:
    # Given: current exports plus a stale partial artifact share the export root.
    exports_dir = tmp_path / "exports"
    registry = ExportRegistry(exports_dir, FakeExportClock(NOW))
    first = registry.allocate()
    second = registry.allocate()
    _ = first.path.write_bytes(b"first")
    _ = second.path.write_bytes(b"second")
    stale_temporary = exports_dir / "stale.tmp"
    _ = stale_temporary.write_bytes(b"partial")

    # When: a successful database reset invalidates all journal-derived exports.
    registry.discard_all()

    # Then: no registered URL or export artifact survives the reset.
    assert registry.resolve(first.export_id) is None
    assert registry.resolve(second.export_id) is None
    assert not first.path.exists()
    assert not second.path.exists()
    assert not stale_temporary.exists()


def test_export_registry_bulk_cleanup_removes_every_expired_artifact(
    tmp_path: Path,
) -> None:
    clock = FakeExportClock(NOW)
    registry = ExportRegistry(tmp_path / "bulk-expiry", clock)
    artifacts = (registry.allocate(), registry.allocate())
    for artifact in artifacts:
        _ = artifact.path.write_bytes(b"csv")

    clock.advance(timedelta(hours=24))

    assert registry.cleanup_expired() == 2
    assert all(not artifact.path.exists() for artifact in artifacts)
