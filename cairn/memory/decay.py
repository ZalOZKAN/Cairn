"""
Memory decay, time-based importance reduction.

score = importance × 2^(−days/half_life)

Forgetting-curve logic: older facts are not erased, but their importance
fades. During recall, this score is the ranking criterion.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


DEFAULT_HALF_LIFE_DAYS = 30.0


def _parse_ts(timestamp: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    # Legacy naive timestamps assumed to be UTC
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def days_old(timestamp: str, now: datetime | None = None) -> float:
    """Days elapsed since the given ISO timestamp (UTC-aware)."""
    ts = _parse_ts(timestamp)
    if ts is None:
        return 0.0
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


def decayed_score(
    importance: float,
    timestamp: str,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    now: datetime | None = None,
) -> float:
    """Weight importance by time-based decay."""
    if half_life_days <= 0:
        return float(importance)
    days = days_old(timestamp, now)
    return float(importance) * math.pow(2.0, -days / half_life_days)
