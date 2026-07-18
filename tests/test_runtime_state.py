from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from custom_components.geofence_journal.models import PresenceState
from custom_components.geofence_journal.runtime.state import (
    RuntimeInvariantError,
    active_deadline,
    confirmation_seconds,
    confirmed_state,
    direction_cooldown,
    event_type_for,
)
from custom_components.geofence_journal.storage.records import RuntimeStateRecord
from tests.test_runtime_fixtures import runtime_resources

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def pending_outside_with_enter_cooldown() -> RuntimeStateRecord:
    """Build an inconsistent stable state that requires correction without an event."""
    return RuntimeStateRecord(
        rule_id="rule-1",
        presence_state=PresenceState.OUTSIDE,
        last_event_id="event-1",
        last_event_type=None,
        last_event_at=NOW - timedelta(minutes=1),
        enter_cooldown_until=NOW + timedelta(minutes=2),
        exit_cooldown_until=None,
        pending_transition=PresenceState.INSIDE,
        pending_started_at=NOW - timedelta(minutes=2),
        pending_deadline=NOW,
        pending_generation=2,
        latest_observation_at=NOW,
        latest_coordinates=None,
        latest_accuracy_m=None,
        last_processed_at=NOW,
        updated_at=NOW,
    )


def test_suppressed_correction_preserves_existing_cooldown_deadline() -> None:
    # Given
    state = pending_outside_with_enter_cooldown()

    # When
    corrected = confirmed_state(
        state,
        PresenceState.INSIDE,
        NOW,
        None,
        runtime_resources().rule,
    )

    # Then
    assert corrected.presence_state is PresenceState.INSIDE
    assert corrected.enter_cooldown_until == NOW + timedelta(minutes=2)


def test_cooldown_expires_at_exact_utc_boundary() -> None:
    # Given
    deadline = NOW

    # When
    recovered = active_deadline(deadline, NOW)

    # Then
    assert recovered is None


def test_unknown_direction_has_no_confirmation_or_cooldown() -> None:
    state = pending_outside_with_enter_cooldown()

    assert confirmation_seconds(runtime_resources().rule, PresenceState.UNKNOWN) == 0
    assert direction_cooldown(state, PresenceState.UNKNOWN) is None


def test_unknown_direction_cannot_create_a_confirmed_event() -> None:
    state = pending_outside_with_enter_cooldown()

    with pytest.raises(
        RuntimeInvariantError, match="unknown cannot be confirmed"
    ) as exc:
        _ = event_type_for(PresenceState.UNKNOWN)
    assert str(exc.value) == "unknown cannot be confirmed"
    with pytest.raises(RuntimeInvariantError, match="unknown cannot be confirmed"):
        _ = confirmed_state(
            state,
            PresenceState.UNKNOWN,
            NOW,
            "invalid-event",
            runtime_resources().rule,
        )
