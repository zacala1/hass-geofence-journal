from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from anyio import Event, create_task_group, fail_after
from anyio.from_thread import run, run_sync
from anyio.lowlevel import checkpoint
from custom_components.geofence_journal.storage import (
    AsyncSQLiteStore,
    StorageClosedError,
)
from custom_components.geofence_journal.storage.db_types import (
    SQLConnection,
    required_integer,
)
from custom_components.geofence_journal.storage.errors import StorageError

if TYPE_CHECKING:
    from pathlib import Path


def _blocking_read(connection: SQLConnection, started: Event, release: Event) -> int:
    cursor = connection.execute("SELECT version FROM schema_version")
    run_sync(started.set)
    run(release.wait)
    row = cursor.fetchone()
    assert row is not None
    return required_integer(row[0], field="schema version")


def _insert_journal(connection: SQLConnection) -> None:
    _ = connection.execute(
        """INSERT INTO journals(
        id,name,enabled,created_at,updated_at
        ) VALUES(?,?,?,?,?)""",
        (
            "journal-1",
            "Concurrent journal",
            1,
            "2026-07-18T12:00:00Z",
            "2026-07-18T12:00:00Z",
        ),
    )


@pytest.mark.asyncio
async def test_independent_reader_does_not_block_runtime_writes(
    tmp_path: Path,
) -> None:
    # Given: one admitted read snapshot held open by an export-like operation.
    store = AsyncSQLiteStore(tmp_path / "reader-write.db")
    await store.async_open()
    started = Event()
    release = Event()

    def blocking_read(connection: SQLConnection) -> int:
        return _blocking_read(connection, started, release)

    async with create_task_group() as task_group:
        _ = task_group.start_soon(store.async_run_read_operation, blocking_read)
        await started.wait()

        # When: a normal runtime write is accepted during that snapshot.
        with fail_after(1):
            await store.async_run_operation(_insert_journal)

        # Then: the write completes without waiting for the reader to finish.
        count = await store.async_run_operation(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM journals"
            ).fetchone()
        )
        assert count == (1,)
        release.set()

    await store.async_close()


@pytest.mark.asyncio
async def test_second_reader_is_rejected_instead_of_queued(
    tmp_path: Path,
) -> None:
    # Given: one long-running export reader already owns the bounded slot.
    store = AsyncSQLiteStore(tmp_path / "bounded-reader.db")
    await store.async_open()
    started = Event()
    release = Event()

    def blocking_read(connection: SQLConnection) -> int:
        return _blocking_read(connection, started, release)

    async with create_task_group() as task_group:
        _ = task_group.start_soon(store.async_run_read_operation, blocking_read)
        await started.wait()

        # When / Then: a second export fails fast instead of growing a wait queue.
        with pytest.raises(StorageError, match="reader is busy"):
            _ = await store.async_run_read_operation(
                lambda connection: connection.execute(
                    "SELECT version FROM schema_version"
                ).fetchone()
            )
        release.set()

    await store.async_close()


@pytest.mark.asyncio
async def test_close_waits_for_admitted_reader_and_rejects_future_reads(
    tmp_path: Path,
) -> None:
    # Given: a read operation admitted before shutdown starts.
    store = AsyncSQLiteStore(tmp_path / "reader-close.db")
    await store.async_open()
    started = Event()
    release = Event()
    close_requested = Event()
    close_done = Event()

    def blocking_read(connection: SQLConnection) -> int:
        return _blocking_read(connection, started, release)

    async def close_store() -> None:
        close_requested.set()
        await store.async_close()
        close_done.set()

    async with create_task_group() as task_group:
        _ = task_group.start_soon(store.async_run_read_operation, blocking_read)
        await started.wait()
        _ = task_group.start_soon(close_store)
        await close_requested.wait()
        await checkpoint()

        # When: close is waiting for the active reader.
        assert not close_done.is_set()
        release.set()
        await close_done.wait()

    # Then: the completed close rejects any later read admission.
    with pytest.raises(StorageClosedError):
        _ = await store.async_run_read_operation(
            lambda connection: connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone()
        )
