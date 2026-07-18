from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final

from custom_components.geofence_journal.export import ExportRegistry

if TYPE_CHECKING:
    from pathlib import Path

NOW: Final = datetime(2026, 7, 18, 12, tzinfo=UTC)


@final
class FixedClock:
    def utc_now(self) -> datetime:
        return NOW


def test_export_registry_serializes_parallel_lifecycle_operations(
    tmp_path: Path,
) -> None:
    # Given: downloads, expiry cleanup, and reset share one process registry.
    registry = ExportRegistry(tmp_path / "exports", FixedClock())

    def create_and_resolve(index: int) -> str:
        artifact = registry.allocate()
        _ = artifact.path.write_text(str(index), encoding="utf-8")
        assert registry.resolve(artifact.export_id) == artifact
        _ = registry.cleanup_expired()
        return artifact.export_id

    # When: several executor workers interleave registry and file operations.
    with ThreadPoolExecutor(max_workers=8) as executor:
        identifiers = tuple(executor.map(create_and_resolve, range(64)))
        resolved = tuple(executor.map(registry.resolve, identifiers))

    # Then: no mutation race loses an artifact, and reset cleanup remains complete.
    assert all(artifact is not None for artifact in resolved)
    registry.discard_all()
    assert all(registry.resolve(identifier) is None for identifier in identifiers)
    assert list((tmp_path / "exports").glob("*.csv")) == []
