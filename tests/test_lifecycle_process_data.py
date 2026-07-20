from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal.export import ExportRegistry
from custom_components.geofence_journal.lifecycle import RuntimePauseHandle
from custom_components.geofence_journal.process_data import IntegrationProcessData
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 7, 20, 1, 2, tzinfo=UTC)


class FixedClock:
    def utc_now(self) -> datetime:
        return NOW


def test_process_data_accepts_export_registry_and_empty_backup_pause(
    tmp_path: Path,
) -> None:
    registry = ExportRegistry(tmp_path / "exports", FixedClock())

    process_data = IntegrationProcessData(exports=registry)

    assert process_data.exports is registry
    assert process_data.backup_pause is None


def test_process_data_accepts_a_typed_backup_pause(tmp_path: Path) -> None:
    registry = ExportRegistry(tmp_path / "exports", FixedClock())
    pause = RuntimePauseHandle.create(reason="home-assistant-backup")

    process_data = IntegrationProcessData(exports=registry, backup_pause=pause)

    assert process_data.backup_pause is pause


def test_process_data_rejects_missing_exports() -> None:
    with pytest.raises(ValidationError):
        _ = IntegrationProcessData.model_validate({})
