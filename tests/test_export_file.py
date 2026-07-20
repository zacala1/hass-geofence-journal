from __future__ import annotations

import os
from typing import TYPE_CHECKING, Never

from custom_components.geofence_journal import export_file
from custom_components.geofence_journal.export_file import (
    is_regular_file_without_links,
    open_verified_regular_file,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _is_closed(descriptor: int) -> bool:
    try:
        _ = os.fstat(descriptor)
    except OSError:
        return True
    return False


def test_verified_open_accepts_regular_file_and_rejects_other_paths(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.csv"
    _ = artifact.write_bytes(b"verified")
    directory = tmp_path / "directory"
    directory.mkdir()

    assert is_regular_file_without_links(artifact)
    stream = open_verified_regular_file(artifact)
    assert stream is not None
    with stream:
        assert stream.read() == b"verified"

    assert not is_regular_file_without_links(tmp_path / "missing.csv")
    assert open_verified_regular_file(tmp_path / "missing.csv") is None
    assert not is_regular_file_without_links(directory)
    assert open_verified_regular_file(directory) is None


def test_verified_open_handles_descriptor_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.csv"
    _ = artifact.write_bytes(b"verified")

    def fail_open(_path: Path, _flags: int) -> Never:
        raise OSError

    monkeypatch.setattr(os, "open", fail_open)

    assert open_verified_regular_file(artifact) is None


def test_verified_open_closes_descriptor_for_a_replaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.csv"
    replacement = tmp_path / "replacement.csv"
    _ = artifact.write_bytes(b"verified")
    _ = replacement.write_bytes(b"replacement")
    descriptor = os.open(replacement, os.O_RDONLY)

    def open_replacement(_path: Path) -> int:
        return descriptor

    monkeypatch.setattr(export_file, "_open_descriptor", open_replacement)

    assert open_verified_regular_file(artifact) is None
    assert _is_closed(descriptor)


def test_verified_open_handles_descriptor_stat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.csv"
    _ = artifact.write_bytes(b"verified")
    descriptor = os.open(artifact, os.O_RDONLY)

    def return_descriptor(_path: Path) -> int:
        return descriptor

    def fail_fstat(_descriptor: int) -> Never:
        raise OSError

    monkeypatch.setattr(export_file, "_open_descriptor", return_descriptor)
    monkeypatch.setattr(os, "fstat", fail_fstat)

    assert open_verified_regular_file(artifact) is None
    monkeypatch.undo()
    assert _is_closed(descriptor)


def test_verified_open_closes_descriptor_when_stream_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.csv"
    _ = artifact.write_bytes(b"verified")
    descriptor = os.open(artifact, os.O_RDONLY)

    def return_descriptor(_path: Path) -> int:
        return descriptor

    def fail_fdopen(_descriptor: int, _mode: str) -> Never:
        raise OSError

    monkeypatch.setattr(export_file, "_open_descriptor", return_descriptor)
    monkeypatch.setattr(os, "fdopen", fail_fdopen)

    assert open_verified_regular_file(artifact) is None
    assert _is_closed(descriptor)
