"""Shared test fakes for workflow-module regression tests.

Deliberately never connects to a real database or PVE cluster, even for
reads. A permanent, checked-in test must not depend on production
Postgres/PVE reachability, and must never be able to accidentally
mutate production state no matter how it's invoked later (CI, cron,
another engineer). Lesson learned live during this same audit-fix
session: an ad-hoc verification script for finding #10 used the real
SessionLocal() + real PVE client and ended up re-issuing a genuine
(harmless, idempotent, but unintended) PUT against the real
nightly-backup job. These fakes exist so that mistake can't recur here.
"""

from unittest.mock import MagicMock


def make_query_router(rows_by_model):
    """A minimal stand-in for db.query(Model)...filter(...).one()/.all()/
    etc. Ignores the actual filter predicate -- tests seed exactly the
    row(s) each query should see, keyed by which Model class is queried,
    rather than re-implementing SQL filtering. This is safe as long as a
    given code path queries each model at most once with one intended
    meaning per test, which holds for every function these tests cover."""

    def query(model):
        q = MagicMock()
        rows = rows_by_model.get(model, [])
        q.filter.return_value = q
        q.order_by.return_value = q
        q.one.return_value = rows[0] if rows else None
        q.one_or_none.return_value = rows[0] if rows else None
        q.first.return_value = rows[0] if rows else None
        q.all.return_value = list(rows)
        q.count.return_value = len(rows)
        return q

    return query


class FakeOperation:
    """Mutable stand-in for the Operation ORM model -- just the
    attributes the workflow functions under test actually read or set."""

    def __init__(self, **kwargs):
        import uuid

        self.id = kwargs.get("id", uuid.uuid4())
        self.status = kwargs["status"]
        self.stage = kwargs.get("stage")
        self.cluster_id = kwargs.get("cluster_id")
        self.node_id = kwargs.get("node_id")
        self.workload_id = kwargs.get("workload_id")
        self.context = kwargs.get("context", {})
        self.dry_run_result = kwargs.get("dry_run_result")
        self.verification_result = None
        self.precondition_snapshot = None
        self.error = None
        self.blocking_safety_rules = None
        self.pve_upid = None


def fake_enter_stage(db, op, *, status, stage=None, **kwargs):
    op.status = status
    if stage is not None:
        op.stage = stage
    for k, v in kwargs.items():
        setattr(op, k, v)
    return op


def fake_fail_operation(db, op, *, error, actor="system"):
    op.status = "failed"
    op.error = error
    return op


def fake_block_operation(db, op, *, blocking_safety_rules, actor="system"):
    op.status = "blocked"
    op.blocking_safety_rules = blocking_safety_rules
    return op


def fake_pve_client(jobs):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.backup_jobs = MagicMock(return_value=jobs)
    return client
