import logging
import os
import threading
import time

import redis
from rq import Queue, Worker
from sqlalchemy.exc import OperationalError

from pyxie_core.db import SessionLocal
from pyxie_core.models import AppSettings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pyxie-worker")

REDIS_URL = os.environ["REDIS_URL"]
conn = redis.from_url(REDIS_URL)

# Phase 0 queue topology: polling is used today; analysis is listened-to but
# has no jobs yet. Stage W0/W1 gives 'operations' its first producer (the
# /api/operations/{id}/approve endpoint) and consumer (this worker, below) --
# it is now the only queue whose jobs can reach a PVE write, and only via
# execute_operation_job -> a per-operation-type workflow module.
POLLING_QUEUE = "polling"
ANALYSIS_QUEUE = "analysis"
OPERATIONS_QUEUE = "operations"

polling_q = Queue(POLLING_QUEUE, connection=conn)
analysis_q = Queue(ANALYSIS_QUEUE, connection=conn)


def _wait_for_db():
    while True:
        try:
            db = SessionLocal()
            db.query(AppSettings).count()
            db.close()
            return
        except OperationalError:
            log.info("database not ready yet, retrying in 3s...")
            time.sleep(3)


def _get_interval_seconds() -> int:
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
        return row.inventory_refresh_interval_seconds if row else 300
    finally:
        db.close()


PRUNE_EVERY_N_CYCLES = 288  # once a day at the default 5-minute interval
REPORT_COLLECT_EVERY_N_CYCLES = 6  # reporting inventory: every 30 min at the default interval (first pass on startup)


def scheduler_loop():
    _wait_for_db()
    log.info("scheduler started")
    cycle = 0
    while True:
        try:
            interval = _get_interval_seconds()
        except Exception:
            interval = 300
        log.info("enqueuing run_all (discovery -> protection -> findings -> recommendations), next run in %ss", interval)
        polling_q.enqueue("worker.jobs.run_all", job_timeout=300)
        if cycle % REPORT_COLLECT_EVERY_N_CYCLES == 0:
            analysis_q.enqueue("worker.jobs.collect_reporting_job", job_timeout=1800)
        cycle += 1
        if cycle % PRUNE_EVERY_N_CYCLES == 0:
            polling_q.enqueue("worker.jobs.prune_metrics_job", job_timeout=120)
        time.sleep(interval)


def live_memory_loop():
    """Keeps the RAM meter within ~30s of PVE's own summary screen. Separate from the
    heavy 5-minute sync so it is never held up by it; a failure only means the meter
    falls back to the last sync's value (readers ignore rows older than 3 minutes)."""
    from pyxie_core.live_memory import INTERVAL_SECONDS, refresh_live_memory

    _wait_for_db()
    log.info("live memory refresher started (every %ss)", INTERVAL_SECONDS)
    failures = 0
    while True:
        db = SessionLocal()
        try:
            refresh_live_memory(db)
            failures = 0
        except Exception:
            db.rollback()
            failures += 1
            if failures in (1, 10) or failures % 120 == 0:  # don't flood the log if PVE is down
                log.exception("live memory refresh failed (%s in a row)", failures)
        finally:
            db.close()
        time.sleep(INTERVAL_SECONDS)


def liveness_loop():
    """Probes every running VM's QEMU about once a minute so a hung VM is noticed within minutes,
    not when a live migration hangs. Its own thread and table, like the live-memory refresher; a
    failure only means results go stale (findings and readers ignore stale rows)."""
    from pyxie_core.liveness_watch import INTERVAL_SECONDS, refresh_liveness

    _wait_for_db()
    log.info("liveness watcher started (every %ss)", INTERVAL_SECONDS)
    failures = 0
    while True:
        started = time.monotonic()
        db = SessionLocal()
        try:
            result = refresh_liveness(db)
            failures = 0
            if result.get("bad"):
                log.warning("liveness: %s of %s running VMs not answering normally", result["bad"], result["probed"])
        except Exception:
            db.rollback()
            failures += 1
            if failures in (1, 10) or failures % 60 == 0:
                log.exception("liveness refresh failed (%s in a row)", failures)
        finally:
            db.close()
        time.sleep(max(5.0, INTERVAL_SECONDS - (time.monotonic() - started)))


def main():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    threading.Thread(target=live_memory_loop, daemon=True).start()
    threading.Thread(target=liveness_loop, daemon=True).start()

    try:
        from worker.jobs import resume_inflight_operations
        result = resume_inflight_operations()
        log.info("resume_inflight_operations on startup: %s", result)
    except Exception:
        log.exception("resume_inflight_operations failed on startup -- any mid-flight operations "
                       "will stay in their current status until manually re-enqueued or the next restart")

    log.info("rq worker listening on: %s, %s, %s", POLLING_QUEUE, ANALYSIS_QUEUE, OPERATIONS_QUEUE)
    worker = Worker([OPERATIONS_QUEUE, POLLING_QUEUE, ANALYSIS_QUEUE], connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
