from custom_components.geofence_journal.lifecycle import (
    ResourceGenerationStartupError,
    RuntimePauseHandle,
    RuntimePauseTokenError,
)


def test_pause_token_error_omits_opaque_token() -> None:
    handle = RuntimePauseHandle.create(reason="duplicate-resume")

    error = RuntimePauseTokenError(handle)

    assert error.handle is handle
    assert str(error) == "invalid or consumed runtime pause handle (duplicate-resume)"
    assert str(handle.token) not in str(error)


def test_resource_generation_error_reports_failed_stage() -> None:
    error = ResourceGenerationStartupError("listener-registration")

    assert error.stage == "listener-registration"
    assert str(error) == (
        "resource generation startup failed during listener-registration"
    )
