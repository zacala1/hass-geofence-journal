"""AnyIO adapter that keeps blocking SQLite work off the event loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

import anyio
from anyio.to_thread import run_sync

from .errors import StorageClosedError
from .repository import DEFAULT_BUSY_TIMEOUT_MS, SQLiteStore

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from .records import ConfirmedTransition, TransitionResult
    from .resources import ConfiguredResources


@final
class AsyncSQLiteStore:
    """Serialize accepted operations and drain them before closing."""

    def __init__(
        self, path: Path, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
    ) -> None:
        """Configure the synchronous store and lifecycle gate."""
        self._store = SQLiteStore(path, busy_timeout_ms=busy_timeout_ms)
        self._gate = anyio.Lock()
        self._opened = False
        self._closing = False

    async def async_open(self) -> None:
        """Open storage in a worker thread."""
        async with self._gate:
            if self._closing:
                raise StorageClosedError
            if not self._opened:
                _ = await run_sync(self._store.open)
                self._opened = True

    async def async_upsert_resources(
        self, resources: ConfiguredResources, timestamp: datetime
    ) -> None:
        """Persist one complete resource linkage off-loop."""
        async with self._gate:
            self._ensure_accepting()
            await run_sync(lambda: self._upsert_resources(resources, timestamp))

    async def async_confirm_transition(
        self, transition: ConfirmedTransition
    ) -> TransitionResult:
        """Commit a transition off-loop while holding the lifecycle gate."""
        async with self._gate:
            self._ensure_accepting()
            return await run_sync(self._store.confirm_transition, transition)

    async def async_close(self) -> None:
        """Stop accepting work, drain the gate, and close off-loop."""
        async with self._gate:
            self._closing = True
            if self._opened:
                with anyio.CancelScope(shield=True):
                    await run_sync(self._store.close)
                self._opened = False

    def _ensure_accepting(self) -> None:
        if not self._opened or self._closing:
            raise StorageClosedError

    def _upsert_resources(
        self, resources: ConfiguredResources, timestamp: datetime
    ) -> None:
        self._store.upsert_tracker(resources.tracker, timestamp)
        self._store.upsert_place(resources.place, timestamp)
        self._store.upsert_journal(resources.journal, timestamp)
        self._store.upsert_rule(resources.rule, timestamp)
