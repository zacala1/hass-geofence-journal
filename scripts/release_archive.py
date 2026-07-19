"""Build the deterministic manual-install release archive."""

from __future__ import annotations

from typing import TYPE_CHECKING, final, override
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from scripts.release_contract import (
    PROJECT_NAME,
    check_release,
)
from scripts.release_errors import (
    ReleaseArtifactError,
    ReleaseCheckError,
    ReleaseOutputDirectoryError,
)

if TYPE_CHECKING:
    from pathlib import Path


@final
class ReleaseSymlinkError(ReleaseCheckError):
    """The integration contains a link outside the release file contract."""

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        """Store the rejected source path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        """Render the rejected source path."""
        return f"release source is a symlink: {self.path}"


def build_release(root: Path, output_directory: Path) -> Path:
    """Create one deterministic manual-install ZIP from validated sources."""
    contract = check_release(root)
    destination = output_directory.resolve()
    integration = contract.root / "custom_components" / contract.domain
    if destination == integration or integration in destination.parents:
        raise ReleaseOutputDirectoryError(destination, integration)
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReleaseArtifactError(destination) from error
    archive_path = destination / f"{PROJECT_NAME}-v{contract.version}.zip"
    sources = _archive_sources(integration)
    try:
        with ZipFile(archive_path, "w") as archive:
            for source in sources:
                relative = source.relative_to(contract.root).as_posix()
                info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(
                    info,
                    source.read_bytes(),
                    compress_type=ZIP_DEFLATED,
                    compresslevel=9,
                )
    except OSError as error:
        raise ReleaseArtifactError(archive_path) from error
    return archive_path


def _archive_sources(integration: Path) -> tuple[Path, ...]:
    sources: list[Path] = []
    for source in sorted(integration.rglob("*")):
        if source.is_symlink():
            raise ReleaseSymlinkError(source)
        if (
            source.is_file()
            and "__pycache__" not in source.parts
            and source.suffix not in {".pyc", ".pyo"}
        ):
            sources.append(source)
    return tuple(sources)
