"""Typed primitives for runtime generation and pause lifecycles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final, override
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class RuntimePauseHandle:
    """Opaque capability required to resume one runtime pause."""

    token: UUID
    reason: str

    @classmethod
    def create(cls, *, reason: str) -> RuntimePauseHandle:
        """Create a unique pause capability with a diagnostic reason."""
        return cls(token=uuid4(), reason=reason)


@final
class RuntimePauseTokenError(RuntimeError):
    """A pause handle was already consumed or never belonged to the manager."""

    __slots__ = ("handle",)

    def __init__(self, handle: RuntimePauseHandle) -> None:
        """Retain the rejected handle for diagnostics."""
        super().__init__(handle)
        self.handle = handle

    @override
    def __str__(self) -> str:
        """Render a stable message without exposing the opaque token."""
        return f"invalid or consumed runtime pause handle ({self.handle.reason})"


@final
class ResourceGenerationStartupError(RuntimeError):
    """A staged listener generation could not be started safely."""

    __slots__ = ("stage",)

    def __init__(self, stage: str) -> None:
        """Retain the failed lifecycle stage."""
        super().__init__(stage)
        self.stage = stage

    @override
    def __str__(self) -> str:
        """Render the failed lifecycle stage."""
        return f"resource generation startup failed during {self.stage}"


def attach_secondary_failure(
    primary: BaseException,
    secondary: BaseException,
    *,
    operation: str,
) -> None:
    """Retain a cleanup failure without replacing the primary exception."""
    primary.add_note(f"{operation} also failed: {secondary!r}")
