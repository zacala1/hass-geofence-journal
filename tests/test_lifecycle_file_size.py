from __future__ import annotations

from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
SCANNED_ROOTS: Final = (
    PROJECT_ROOT / "custom_components" / "geofence_journal",
    PROJECT_ROOT / "tests",
)
MAX_PURE_LINES: Final = 250
ENGINE_MAX_PURE_LINES: Final = 248


def _pure_line_count(path: Path) -> int:
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def test_python_modules_remain_small_enough_for_focused_review() -> None:
    oversized = {
        path.relative_to(PROJECT_ROOT): _pure_line_count(path)
        for root in SCANNED_ROOTS
        for path in root.rglob("*.py")
        if _pure_line_count(path) > MAX_PURE_LINES
    }

    assert oversized == {}


def test_runtime_engine_keeps_lifecycle_orchestration_out() -> None:
    engine = (
        PROJECT_ROOT
        / "custom_components"
        / "geofence_journal"
        / "runtime"
        / "engine.py"
    )

    assert _pure_line_count(engine) <= ENGINE_MAX_PURE_LINES
