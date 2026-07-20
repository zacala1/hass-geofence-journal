"""Build the deterministic manual-install release archive."""

from __future__ import annotations

from typing import TYPE_CHECKING
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from scripts.release_contract import (
    RELEASE_FILENAME,
    check_release,
)
from scripts.release_errors import (
    ReleaseArtifactError,
    ReleaseOutputDirectoryError,
)
from scripts.release_sources import validated_release_sources

if TYPE_CHECKING:
    from pathlib import Path


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
    archive_path = destination / RELEASE_FILENAME
    sources = validated_release_sources(contract.root, integration)
    try:
        with ZipFile(archive_path, "w") as archive:
            for source in sources:
                relative = source.relative_to(integration).as_posix()
                info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
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
