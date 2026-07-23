from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID

import pytest
from custom_components.geofence_journal.models import (
    CoordinatePlace,
    Coordinates,
    JournalDefinition,
    JournalId,
    Meters,
    PlaceId,
    RuleDefinition,
    RuleId,
    Seconds,
    TrackerDefinition,
    TrackerId,
    TrackerKind,
)
from custom_components.geofence_journal.resource_catalog import (
    DeleteResourceRequest,
    JournalResourceItem,
    PlaceResourceItem,
    ResourceInUseError,
    ResourceNotFoundError,
    ResourceType,
    RuleResourceItem,
    TrackerResourceItem,
)
from custom_components.geofence_journal.storage import SQLiteStore
from custom_components.geofence_journal.storage.resource_catalog import (
    delete_resource,
    get_resource,
    list_resources,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW: Final = datetime(2026, 7, 23, 12, tzinfo=UTC)
TRACKER_ID: Final = TrackerId("00000000-0000-4000-8000-000000000001")
PLACE_ID: Final = PlaceId("00000000-0000-4000-8000-000000000002")
JOURNAL_ID: Final = JournalId("00000000-0000-4000-8000-000000000003")
RULE_ID: Final = RuleId("00000000-0000-4000-8000-000000000004")


def _seed(store: SQLiteStore) -> None:
    store.upsert_tracker(
        TrackerDefinition(
            tracker_id=TRACKER_ID,
            entity_id="person.alice",
            kind=TrackerKind.PERSON,
            name="Alice",
            enabled=True,
        ),
        NOW,
    )
    store.upsert_place(
        CoordinatePlace(
            PLACE_ID,
            "Home",
            Coordinates(37.5, 127.0),
            Meters(100),
        ),
        NOW,
    )
    store.upsert_journal(
        JournalDefinition(
            journal_id=JOURNAL_ID,
            name="Presence",
            enabled=True,
        ),
        NOW,
    )
    store.upsert_rule(
        RuleDefinition(
            rule_id=RULE_ID,
            tracker_id=TRACKER_ID,
            place_id=PLACE_ID,
            journal_id=JOURNAL_ID,
            enabled=True,
            enter_confirmation_seconds=Seconds(120),
            exit_confirmation_seconds=Seconds(180),
            cooldown_seconds=Seconds(300),
            exit_margin_meters=Meters(50),
            max_gps_accuracy_meters=Meters(200),
        ),
        NOW,
    )


def test_catalog_lists_and_gets_every_resource_shape(tmp_path: Path) -> None:
    with SQLiteStore(tmp_path / "catalog.db") as store:
        _seed(store)
        items = store.run_operation(lambda connection: list_resources(connection, None))
        place = store.run_operation(
            lambda connection: get_resource(
                connection, ResourceType.PLACE, str(PLACE_ID)
            )
        )

    assert len(items) == 4
    assert isinstance(items[0], JournalResourceItem)
    assert isinstance(items[1], PlaceResourceItem)
    assert isinstance(items[2], RuleResourceItem)
    assert isinstance(items[3], TrackerResourceItem)
    assert isinstance(place, PlaceResourceItem)
    assert place.latitude == 37.5
    assert place.exit_margin_meters == 50


def test_catalog_delete_is_atomic_and_rejects_referenced_resources(
    tmp_path: Path,
) -> None:
    with SQLiteStore(tmp_path / "delete.db") as store:
        _seed(store)
        with pytest.raises(ResourceInUseError, match="tracker"):
            _ = store.run_operation(
                lambda connection: delete_resource(
                    connection, ResourceType.TRACKER, str(TRACKER_ID)
                )
            )

        deleted = store.run_operation(
            lambda connection: delete_resource(
                connection, ResourceType.RULE, str(RULE_ID)
            )
        )
        remaining = store.run_operation(
            lambda connection: list_resources(connection, ResourceType.RULE)
        )

    assert deleted.resource_id == str(RULE_ID)
    assert deleted.resource_type is ResourceType.RULE
    assert remaining == ()


def test_catalog_missing_resource_and_delete_confirmation_are_stable(
    tmp_path: Path,
) -> None:
    missing_id = "00000000-0000-4000-8000-000000000099"
    with (
        SQLiteStore(tmp_path / "missing.db") as store,
        pytest.raises(ResourceNotFoundError, match=missing_id),
    ):
        _ = store.run_operation(
            lambda connection: get_resource(
                connection, ResourceType.JOURNAL, missing_id
            )
        )

    with pytest.raises(ValueError, match="Input should be True"):
        _ = DeleteResourceRequest.model_validate(
            {
                "resource_type": ResourceType.JOURNAL,
                "resource_id": UUID(missing_id),
                "confirm": False,
            }
        )
