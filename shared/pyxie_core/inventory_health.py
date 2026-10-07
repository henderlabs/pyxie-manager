"""Is PyXie's picture of the cluster fresh? Pure helper, used by the Health findings."""

from datetime import datetime, timedelta, timezone
from typing import Optional


def stale_minutes(last_seen: Optional[datetime], now: datetime, interval_seconds: int) -> Optional[int]:
    """Minutes since the inventory last refreshed, when that is far longer than it should be; else None.

    A refresh is expected every `interval_seconds`. Allow generous slack (5 intervals, at least 10 minutes) so a slow
    sync or a brief outage does not raise noise, but a stall (for example a stuck discovery lock) is caught."""
    if last_seen is None:
        return None
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    limit = timedelta(seconds=max(600, 5 * int(interval_seconds or 300)))
    age = now - last_seen
    return int(age.total_seconds() // 60) if age > limit else None
