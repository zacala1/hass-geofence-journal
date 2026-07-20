"""Typed failures raised by the release tooling."""

from __future__ import annotations

from typing import TYPE_CHECKING, final, override

if TYPE_CHECKING:
    from pathlib import Path


class ReleaseCheckError(Exception):
    """Base class for deployment-contract failures."""


@final
class ReleaseRepositoryError(ReleaseCheckError):
    """Git could not inspect the release repository."""

    @override
    def __str__(self) -> str:
        """Render the failed repository inspection."""
        return "cannot inspect release repository with Git"


@final
class ReleaseDirtyTreeError(ReleaseCheckError):
    """The repository contains unpublished source changes."""

    @override
    def __str__(self) -> str:
        """Render the clean-tree release requirement."""
        return "release requires a clean working tree"


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


@final
class ReleaseUnexpectedSourceError(ReleaseCheckError):
    """The integration contains a file outside the runtime allowlist."""

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        """Store the rejected source path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        """Render the rejected source path."""
        return f"unexpected release source: {self.path}"


@final
class RepositoryRootError(ReleaseCheckError):
    """The command did not start at the repository root."""

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        """Store the rejected path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        """Render the invalid root."""
        return f"not a repository root: {self.path}"


@final
class ReleaseEnvironmentError(ReleaseCheckError):
    """The release command is running with an unsupported interpreter."""

    @override
    def __str__(self) -> str:
        """Render the supported interpreter range."""
        return "release tooling requires Python 3.14.2 through 3.14.x"


@final
class ReleaseFileReadError(ReleaseCheckError):
    """A required release file could not be read."""

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        """Store the unreadable path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        """Render the unreadable path."""
        return f"cannot read release file: {self.path}"


@final
class InvalidReleaseMetadataError(ReleaseCheckError):
    """A release metadata file is malformed or has the wrong shape."""

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        """Store the invalid metadata path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        """Render the invalid metadata path."""
        return f"invalid release metadata: {self.path}"


@final
class MissingReleaseFieldError(ReleaseCheckError):
    """A required metadata field was absent."""

    __slots__ = ("field",)

    def __init__(self, field: str) -> None:
        """Store the absent field name."""
        super().__init__(field)
        self.field = field

    @override
    def __str__(self) -> str:
        """Render the absent field name."""
        return f"missing {self.field}"


@final
class ReleaseMismatchError(ReleaseCheckError):
    """Two declarations of one release property disagree."""

    __slots__ = ("actual", "expected", "field")

    def __init__(self, field: str, expected: str, actual: str) -> None:
        """Store the mismatched field and values."""
        super().__init__(field, expected, actual)
        self.field = field
        self.expected = expected
        self.actual = actual

    @override
    def __str__(self) -> str:
        """Render the mismatch."""
        return f"{self.field} mismatch: expected {self.expected}, got {self.actual}"


@final
class MissingReleaseFilesError(ReleaseCheckError):
    """The integration package is incomplete."""

    __slots__ = ("files",)

    def __init__(self, files: tuple[str, ...]) -> None:
        """Store the missing relative paths."""
        super().__init__(*files)
        self.files = files

    @override
    def __str__(self) -> str:
        """Render the missing relative paths."""
        return f"release integration files missing: {', '.join(self.files)}"


@final
class ReleaseOutputDirectoryError(ReleaseCheckError):
    """The archive destination overlaps the integration source."""

    __slots__ = ("integration", "output")

    def __init__(self, output: Path, integration: Path) -> None:
        """Store the overlapping output and source paths."""
        super().__init__(output, integration)
        self.output = output
        self.integration = integration

    @override
    def __str__(self) -> str:
        """Render the invalid output directory."""
        return (
            "release output directory must be outside integration source: "
            f"{self.output}"
        )


@final
class ReleaseArtifactError(ReleaseCheckError):
    """The release archive could not be created."""

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        """Store the failed output path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        """Render the failed output path."""
        return f"cannot create release artifact: {self.path}"
