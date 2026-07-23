from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from custom_components.geofence_journal.disk_space import (
    MINIMUM_FREE_BYTES,
    InsufficientDiskSpaceError,
    compact_headroom_bytes,
    export_headroom_bytes,
    require_free_space,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_headroom_estimates_are_bounded_and_operation_specific() -> None:
    assert export_headroom_bytes(1) == MINIMUM_FREE_BYTES
    assert compact_headroom_bytes(1) == MINIMUM_FREE_BYTES
    assert export_headroom_bytes(MINIMUM_FREE_BYTES) > MINIMUM_FREE_BYTES
    assert compact_headroom_bytes(MINIMUM_FREE_BYTES) > MINIMUM_FREE_BYTES


def test_free_space_error_reports_only_capacity_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def free_bytes(_path: Path) -> int:
        return 100

    monkeypatch.setattr(
        "custom_components.geofence_journal.disk_space._free_bytes",
        free_bytes,
    )

    with pytest.raises(InsufficientDiskSpaceError) as raised:
        require_free_space(tmp_path / "export.csv", required_bytes=101)

    assert raised.value.available_bytes == 100
    assert raised.value.required_bytes == 101
    assert str(tmp_path) not in str(raised.value)
