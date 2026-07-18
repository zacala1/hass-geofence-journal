from importlib.util import find_spec

from custom_components.geofence_journal import services


def test_service_boundary_module_exists() -> None:
    # Given: Task 6 requires an independently injectable Home Assistant boundary.
    module_name = "custom_components.geofence_journal.services"

    # When: the integration's service module is discovered.
    service_spec = find_spec(module_name)

    # Then: the public boundary exists for Task 5 lifecycle wiring.
    assert service_spec is not None


def test_service_boundary_exposes_typed_registration_contract() -> None:
    # Given: Task 5 must inject a backend without importing its manager here.
    required_names = {
        "ServicesBackend",
        "UpsertTrackerRequest",
        "UpsertPlaceRequest",
        "UpsertJournalRequest",
        "UpsertRuleRequest",
        "async_register_services",
        "async_unregister_services",
    }

    # When: the public service module is inspected.
    exposed_names = set(dir(services))

    # Then: typed requests and lifecycle hooks form the whole integration seam.
    assert required_names <= exposed_names
