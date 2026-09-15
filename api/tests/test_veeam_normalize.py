import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.veeam_client import normalize_veeam_protection


def test_no_matching_job_is_unknown():
    result = normalize_veeam_protection("vm123", job_states=[], sessions=[])
    assert result["protected"] == "unknown"
    assert result["confidence"] == "insufficient_data"


def test_successful_job_is_protected():
    job_states = [{"lastResult": {"objectsCount": {"vm123": 1}, "result": "Success"}, "lastRun": "2026-09-01T00:00:00Z"}]
    result = normalize_veeam_protection("vm123", job_states=job_states, sessions=[])
    assert result["protected"] == "true"
    assert result["last_successful_job_at"] == "2026-09-01T00:00:00Z"


def test_failed_job_is_not_protected():
    job_states = [{"lastResult": {"objectsCount": {"vm123": 1}, "result": "Failed"}, "lastRun": "2026-09-01T00:00:00Z"}]
    result = normalize_veeam_protection("vm123", job_states=job_states, sessions=[])
    assert result["protected"] == "false"
