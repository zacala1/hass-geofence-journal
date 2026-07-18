"""Typed storage failures."""

from __future__ import annotations

from typing import final, override


class StorageError(Exception):
    """Base class for persistent storage failures."""


@final
class DatabaseSchemaError(StorageError):
    """Existing schema failure; exceptions require mutable traceback state."""

    __slots__ = ("detail",)
    detail: str

    def __init__(self, detail: str) -> None:
        """Initialize the malformed-schema detail."""
        super().__init__(detail)
        self.detail = detail

    @override
    def __str__(self) -> str:
        """Render the schema failure."""
        return f"invalid Geofence Journal schema: {self.detail}"


@final
class UnsupportedSchemaVersionError(StorageError):
    """Future schema failure; exceptions require mutable traceback state."""

    __slots__ = ("found", "supported")
    found: int
    supported: int

    def __init__(self, found: int, supported: int) -> None:
        """Initialize found and supported versions."""
        super().__init__(found, supported)
        self.found = found
        self.supported = supported

    @override
    def __str__(self) -> str:
        """Render the version mismatch."""
        return f"database schema {self.found} is newer than supported {self.supported}"


@final
class StorageClosedError(StorageError):
    """Lifecycle failure; exceptions require mutable traceback state."""

    @override
    def __str__(self) -> str:
        """Render the lifecycle failure."""
        return "storage is closed or closing"


@final
class InjectedStorageFaultError(StorageError):
    """Test fault; exceptions require mutable traceback state."""

    __slots__ = ("stage",)
    stage: str

    def __init__(self, stage: str) -> None:
        """Initialize the injected failure stage."""
        super().__init__(stage)
        self.stage = stage

    @override
    def __str__(self) -> str:
        """Render the injected stage."""
        return f"injected storage fault at {self.stage}"
