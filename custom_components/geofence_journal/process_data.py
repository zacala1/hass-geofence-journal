"""Process-lifetime data retained outside an individual config entry."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from .export import ExportRegistry  # noqa: TC001  # Pydantic resolves at runtime.
from .lifecycle import (  # noqa: TC001  # Pydantic resolves at runtime.
    RuntimePauseHandle,
)


class IntegrationProcessData(BaseModel):
    """Registry retained by non-removable integration-wide facilities."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True, frozen=True
    )

    exports: ExportRegistry
    backup_pause: RuntimePauseHandle | None = None
