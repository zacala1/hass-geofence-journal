"""Public management input limits that bound storage and runtime work."""

from typing import Final

MAX_NAME_LENGTH: Final = 128
MAX_ENTITY_ID_LENGTH: Final = 255
MAX_NOTE_LENGTH: Final = 4_096
MAX_REASON_LENGTH: Final = 512
MAX_RADIUS_METERS: Final = 1_000_000.0
MAX_EXIT_MARGIN_METERS: Final = 1_000_000.0
MAX_CONFIRMATION_SECONDS: Final = 86_400
MAX_COOLDOWN_SECONDS: Final = 604_800
MAX_GPS_ACCURACY_METERS: Final = 100_000.0
MAX_RETENTION_DAYS: Final = 36_500
