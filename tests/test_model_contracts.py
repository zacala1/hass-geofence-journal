"""Frozen cross-subsystem model contracts."""

from custom_components.geofence_journal.models import PlaceKind


def test_coordinate_place_kind_matches_schema_wire_value() -> None:
    # Given the public place-source enum
    # When its coordinate value crosses the SQLite/service boundary
    value = PlaceKind.COORDINATE.value

    # Then it uses the schema-v1 value from the product contract
    assert value == "coordinates"
