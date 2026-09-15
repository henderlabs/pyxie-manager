"""Capacity analysis: allocated vs. observed, per node and per cluster.
Read-only, informational -- see placement.py for suggestion logic that
builds on top of this.
"""

from .metrics import observation_stats
from .models import Cluster, Node, Workload

NODE_CPU_THREADS_UNKNOWN_DEFAULT = None  # PVE node status doesn't expose thread count cleanly via what we collect


def node_capacity(db, node: Node) -> dict:
    workloads = db.query(Workload).filter(Workload.node_id == node.id, Workload.is_missing.is_(False)).all()
    allocated_vcpu = sum((w.cpu_cores or 0) for w in workloads)
    allocated_mem = sum((w.memory_bytes or 0) for w in workloads)

    cpu_obs = observation_stats(db, "node", node.id, "cpu_pct", window_days=14)
    mem_obs = observation_stats(db, "node", node.id, "mem_pct", window_days=14)

    return {
        "node_id": node.id,
        "name": node.name,
        "maintenance_mode": node.maintenance_mode,
        "allocated_vcpu": allocated_vcpu,
        "allocated_memory_bytes": allocated_mem,
        "node_memory_total_bytes": node.mem_total_bytes,
        "workload_count": len(workloads),
        "observed_cpu_pct": cpu_obs["avg"],
        "observed_cpu_p95_pct": cpu_obs["p95"],
        "observed_mem_pct": mem_obs["avg"],
        "observed_mem_p95_pct": mem_obs["p95"],
        "memory_headroom_bytes": (node.mem_total_bytes - allocated_mem) if node.mem_total_bytes else None,
    }


def cluster_capacity(db, cluster: Cluster) -> dict:
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).all()
    node_reports = [node_capacity(db, n) for n in nodes]

    total_mem = sum((n.mem_total_bytes or 0) for n in nodes)
    total_allocated_mem = sum(r["allocated_memory_bytes"] for r in node_reports)

    busiest = None
    if node_reports:
        scored = [r for r in node_reports if r["observed_cpu_p95_pct"] is not None]
        if scored:
            busiest = max(scored, key=lambda r: r["observed_cpu_p95_pct"])

    most_constrained_resource = None
    if total_mem:
        mem_pressure = total_allocated_mem / total_mem
        cpu_pressures = [r["observed_cpu_p95_pct"] for r in node_reports if r["observed_cpu_p95_pct"] is not None]
        avg_cpu_pressure = (sum(cpu_pressures) / len(cpu_pressures) / 100) if cpu_pressures else 0
        most_constrained_resource = "memory" if mem_pressure > avg_cpu_pressure else "cpu"

    return {
        "cluster_id": cluster.id,
        "name": cluster.name,
        "nodes": node_reports,
        "total_memory_bytes": total_mem,
        "total_allocated_memory_bytes": total_allocated_mem,
        "memory_allocation_pct": round(total_allocated_mem / total_mem * 100, 1) if total_mem else None,
        "busiest_node": busiest["name"] if busiest else None,
        "most_constrained_resource": most_constrained_resource,
        "workloads_contributing_most_pressure": _top_pressure_workloads(db, nodes),
    }


def _top_pressure_workloads(db, nodes, limit: int = 5) -> list[dict]:
    node_ids = [n.id for n in nodes]
    workloads = db.query(Workload).filter(Workload.node_id.in_(node_ids), Workload.is_missing.is_(False)).all() if node_ids else []
    scored = []
    for w in workloads:
        cpu_obs = observation_stats(db, "workload", w.id, "cpu_pct", window_days=14)
        if cpu_obs["avg"] is None:
            continue
        allocated_share = (w.cpu_cores or 1) * (cpu_obs["avg"] / 100)
        scored.append({"vmid": w.vmid, "name": w.name, "cpu_pressure_score": round(allocated_share, 2)})
    scored.sort(key=lambda s: s["cpu_pressure_score"], reverse=True)
    return scored[:limit]


def compute_capacity(db) -> list[dict]:
    return [cluster_capacity(db, c) for c in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all()]
