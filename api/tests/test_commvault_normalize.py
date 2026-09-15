import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.commvault_client import normalize_commvault_protection


def test_no_matching_jobs_is_unknown():
    result = normalize_commvault_protection("vm123", jobs=[])
    assert result["protected"] == "unknown"


def test_no_completed_jobs_is_false():
    jobs = [{"subclient": {"clientName": "vm123"}, "status": "Failed", "jobEndTime": 100}]
    result = normalize_commvault_protection("vm123", jobs=jobs)
    assert result["protected"] == "false"


def test_latest_completed_job_wins():
    jobs = [
        {"subclient": {"clientName": "vm123"}, "status": "Completed", "jobEndTime": 100},
        {"subclient": {"clientName": "vm123"}, "status": "Completed", "jobEndTime": 200},
        {"subclient": {"clientName": "other"}, "status": "Completed", "jobEndTime": 999},
    ]
    result = normalize_commvault_protection("vm123", jobs=jobs)
    assert result["protected"] == "true"
    assert result["last_successful_job_at"] == 200
