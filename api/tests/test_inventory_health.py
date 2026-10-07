"""Pure coverage for the stale-inventory check."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.inventory_health import stale_minutes

NOW = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)


def test_fresh_inventory_is_fine():
    assert stale_minutes(NOW - timedelta(minutes=2), NOW, 60) is None
    assert stale_minutes(NOW - timedelta(minutes=9), NOW, 60) is None  # under the 10 minute floor


def test_stalled_inventory_reports_minutes():
    assert stale_minutes(NOW - timedelta(minutes=13), NOW, 60) == 13
    assert stale_minutes(NOW - timedelta(hours=2), NOW, 300) == 120


def test_slow_interval_gets_more_slack():
    assert stale_minutes(NOW - timedelta(minutes=20), NOW, 900) is None  # 5 x 15 min = 75 min allowed
    assert stale_minutes(NOW - timedelta(minutes=80), NOW, 900) == 80


def test_unknown_and_naive_times():
    assert stale_minutes(None, NOW, 60) is None
    naive = datetime(2026, 10, 7, 17, 30)  # treated as UTC
    assert stale_minutes(naive, NOW, 60) == 30
