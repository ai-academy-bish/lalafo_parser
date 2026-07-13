"""Time helpers.

lalafo timestamps are absolute Unix seconds (``created_time``, ``updated_time``) —
a welcome contrast to house.kg's decaying relative dates.  We keep the raw epoch
*and* an ISO-8601 UTC string, so a consumer can sort on the integer and read the
string.
"""

from __future__ import annotations

from datetime import datetime, timezone


def epoch_to_iso(epoch: object | None) -> str | None:
    """1782136611 -> '2026-06-02T...Z'.  Returns None for missing/invalid input."""
    if epoch in (None, 0, "0"):
        return None
    try:
        return (
            datetime.fromtimestamp(int(epoch), tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (ValueError, OSError, TypeError):
        return None
