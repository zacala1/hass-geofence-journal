"""Explicit purge operations derived from optional journal retention policy."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, override
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from .maintenance import ServiceRequest
from .models import JournalId
from .storage.db_types import required_integer
from .storage.events import MissingEventReferenceError
from .storage.maintenance import PurgeRequest, PurgeResult, purge_events

if TYPE_CHECKING:
    from datetime import datetime

    from .storage.db_types import SQLConnection


class PurgeRetentionRequest(ServiceRequest):
    """Explicitly apply one journal's configured retention cutoff."""

    journal_id: UUID
    dry_run: bool = True
    confirm: bool = False


class RetentionNotConfiguredError(ValueError):
    """The selected journal has no retention policy."""

    @override
    def __str__(self) -> str:
        """Return an actionable message without journal identity."""
        return "journal retention is not configured"


def purge_configured_retention(
    connection: SQLConnection,
    now: datetime,
    request: PurgeRetentionRequest,
) -> PurgeResult:
    """Purge using a journal policy while preserving dry-run confirmation."""
    journal_id = JournalId(str(request.journal_id))
    row = connection.execute(
        "SELECT retention_days FROM journals WHERE id=?",
        (journal_id,),
    ).fetchone()
    if row is None:
        resource = "journal"
        raise MissingEventReferenceError(resource, journal_id)
    if row[0] is None:
        message = "journal retention is not configured"
        raise RetentionNotConfiguredError(message)
    retention_days = required_integer(row[0], field="journal retention days")
    return purge_events(
        connection,
        PurgeRequest(
            before=now - timedelta(days=retention_days),
            journal_id=journal_id,
            dry_run=request.dry_run,
            confirm=request.confirm,
        ),
    )
