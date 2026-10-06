"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Node } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import MetricChart from "@/components/MetricChart";
import PendingUpdatesRow from "@/components/PendingUpdatesRow";
import { formatBytes, formatUptime } from "@/lib/format";
import { onOperationsChanged } from "@/lib/operationsBus";
import { WrenchIcon } from "@/components/Icons";
import { TIMEFRAMES, type Timeframe } from "@/lib/workloadDetail";
import type { NodeLive, NodeRrdRow } from "@/lib/nodeDetail";
import { LIVENESS_CLASS, LIVENESS_LABEL } from "@/lib/liveness";
import type { Liveness } from "@/lib/workloadDetail";

type NodeLiveness = { checked: number; vms: (Liveness & { workload_id: string; vmid: number; name: string | null })[]; error: string | null };

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-0.5 text-sm">
      <dt className="text-muted shrink-0">{label}</dt>
      <dd className="text-text text-right min-w-0 break-words">{value}</dd>
    </div>
  );
}

function barColor(pct: number | null): string {
  if (pct == null) return "#8b95a7";
  return pct >= 90 ? "#e5484d" : pct >= 75 ? "#e5a94c" : "#2fbf71";
}

function Tile({ label, value, pct, sub }: { label: string; value: React.ReactNode; pct?: number | null; sub?: React.ReactNode }) {
  return (
    <Card>
      <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-1">{label}</div>
      <div className="text-2xl font-semibold text-text">{value}</div>
      {pct !== undefined && (
        <div className="h-1.5 rounded-full bg-border overflow-hidden mt-2">
          <div className="h-full rounded-full" style={{ width: `${Math.min(Math.max(pct ?? 0, 0), 100)}%`, backgroundColor: barColor(pct) }} />
        </div>
      )}
      {sub && <div className="text-xs text-muted mt-1.5">{sub}</div>}
    </Card>
  );
}

function useNodeLive(nodeId: string): NodeLive | null {
  const [live, setLive] = useState<NodeLive | null>(null);
  useEffect(() => {
    const load = () =>
      fetch(`/api/nodes/${nodeId}/live`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d: NodeLive | null) => d && setLive(d))
        .catch(() => {});
    load();
    const iv = setInterval(() => {
      if (!document.hidden) load();
    }, 10000);
    return () => clearInterval(iv);
  }, [nodeId]);
  return live;
}

function useNodeRrd(nodeId: string, timeframe: Timeframe): { rows: NodeRrdRow[]; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ rows: NodeRrdRow[]; error: string | null; loading: boolean }>({ rows: [], error: null, loading: true });
  useEffect(() => {
    let cancelled = false;
    setState({ rows: [], error: null, loading: true });
    const load = () =>
      fetch(`/api/nodes/${nodeId}/rrd?timeframe=${timeframe}`)
        .then((r) => r.json())
        .then((d) => !cancelled && setState({ rows: d.rows || [], error: d.error || null, loading: false }))
        .catch((e) => !cancelled && setState({ rows: [], error: String(e), loading: false }));
    load();
    const iv = setInterval(() => {
      if (!document.hidden) load();
    }, timeframe === "hour" ? 30000 : 120000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [nodeId, timeframe]);
  return state;
}

function useNodeLiveness(nodeId: string): NodeLiveness | null {
  const [data, setData] = useState<NodeLiveness | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetch(`/api/nodes/${nodeId}/liveness`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d: NodeLiveness | null) => d && !cancelled && setData(d))
        .catch(() => {});
    load();
    const iv = setInterval(() => {
      if (!document.hidden) load();
    }, 60000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [nodeId]);
  return data;
}

const col = (rows: NodeRrdRow[], f: (r: NodeRrdRow) => number | undefined) => rows.map((r) => f(r) ?? null);
const rate = (v: number) => `${formatBytes(v)}/s`;

/** Everything live about one host, modelled on PVE's own node Summary page. */
export default function NodeOverview({
  nodeId,
  initialNode,
  rebootRequired,
}: {
  nodeId: string;
  initialNode: Node;
  /** From the host-maintenance readiness check; null when that check could not run. */
  rebootRequired: boolean | null;
}) {
  const [node, setNode] = useState<Node>(initialNode);
  const [timeframe, setTimeframe] = useState<Timeframe>("hour");
  const live = useNodeLive(nodeId);
  const rrd = useNodeRrd(nodeId, timeframe);
  const responsiveness = useNodeLiveness(nodeId);
  const s = live?.status ?? null;

  useEffect(() => {
    const refresh = () =>
      fetch(`/api/nodes/${nodeId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d: Node | null) => d && setNode(d))
        .catch(() => {});
    const iv = setInterval(refresh, 5000);
    const off = onOperationsChanged(refresh);
    return () => {
      clearInterval(iv);
      off();
    };
  }, [nodeId]);

  const times = rrd.rows.map((r) => r.time as number);
  const memTotal = Math.max(0, ...rrd.rows.map((r) => r.memtotal ?? 0));
  const hasArc = rrd.rows.some((r) => (r.arcsize ?? 0) > 1048576);
  const uptime = s?.uptime ?? node.uptime_seconds;
  const pending = node.pending_updates ?? 0;
  const load = s?.loadavg ?? [];

  return (
    <div>
      {node.maintenance_mode && (
        <div className="mb-3 px-3 py-2 rounded border border-warn/40 bg-surface text-sm text-warn flex items-center gap-2">
          <WrenchIcon className="w-4 h-4" />
          <span className="font-medium">
            In maintenance mode{node.maintenance_reason ? ` — ${node.maintenance_reason}` : ""}
            {node.maintenance_mode_since ? ` since ${new Date(node.maintenance_mode_since).toLocaleString()}` : ""}
            {node.maintenance_mode_by ? ` (by ${node.maintenance_mode_by})` : ""}
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-4 text-sm">
        <StatusBadge status={node.is_missing ? "unknown" : node.status} />
        {rebootRequired && (
          <Link href={`/operations/maintenance?node=${nodeId}&action=host-reboots`} className="px-2 py-0.5 rounded text-xs font-medium bg-warn/15 text-warn hover:bg-warn/25" title="A newer kernel or other change is installed but not yet running">
            Reboot required
          </Link>
        )}
        {pending > 0 && <span className="px-2 py-0.5 rounded text-xs font-medium bg-accent/15 text-accent">{pending} update{pending === 1 ? "" : "s"} available</span>}
        <span className="text-muted">
          PVE {s?.pve_version ?? node.pve_version ?? "—"} · kernel {s?.kernel ?? node.kernel_version ?? "—"} · uptime {formatUptime(uptime)}
        </span>
      </div>

      {live?.error && <div className="mb-3 px-3 py-2 rounded border border-warn/40 bg-warn/10 text-warn text-xs">Live PVE read incomplete: {live.error}</div>}

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-4">
        <Tile label="CPU" value={s?.cpu_pct != null ? `${s.cpu_pct.toFixed(0)}%` : node.cpu_usage_pct != null ? `${node.cpu_usage_pct.toFixed(0)}%` : "—"} pct={s?.cpu_pct ?? node.cpu_usage_pct}
          sub={s ? `of ${s.cpu_threads ?? "?"} threads · IO delay ${s.io_delay_pct != null ? `${s.io_delay_pct.toFixed(1)}%` : "—"}` : undefined} />
        <Tile label="RAM" value={s?.mem_pct != null ? `${s.mem_pct.toFixed(0)}%` : node.mem_usage_pct != null ? `${node.mem_usage_pct.toFixed(0)}%` : "—"} pct={s?.mem_pct ?? node.mem_usage_pct}
          sub={s ? `${formatBytes(s.mem_used)} of ${formatBytes(s.mem_total)}` : undefined} />
        <Tile label="Root disk" value={s?.root_pct != null ? `${s.root_pct.toFixed(0)}%` : "—"} pct={s?.root_pct ?? null}
          sub={s ? `${formatBytes(s.root_used)} of ${formatBytes(s.root_total)}` : undefined} />
        <Tile label="Load average" value={load[0] != null ? load[0]!.toFixed(1) : "—"}
          sub={s ? `${load.map((x) => (x != null ? x.toFixed(1) : "—")).join(" / ")} · swap ${formatBytes(s.swap_used)} of ${formatBytes(s.swap_total)}` : "1 / 5 / 15 min"} />
      </div>

      <div className="flex items-center justify-between mb-2">
        <div className="text-xs text-muted">Graphs are PVE's own rrd series for this host, read live.</div>
        <label className="text-sm text-muted flex items-center gap-2">
          Timeframe
          <select className="bg-surface2 border border-border rounded px-2 py-1 text-sm text-text" value={timeframe} onChange={(e) => setTimeframe(e.target.value as Timeframe)}>
            {TIMEFRAMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
      </div>
      {rrd.error ? (
        <div className="text-sm text-warn mb-4">Could not load PVE graphs: {rrd.error}</div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-4">
          <Card>
            <MetricChart title="CPU usage" times={times} loading={rrd.loading} format={(v) => `${v < 10 ? v.toFixed(1) : v.toFixed(0)}%`}
              series={[
                { label: "CPU", color: "#4f8cff", area: true, values: col(rrd.rows, (r) => (r.cpu != null ? r.cpu * 100 : undefined)) },
                { label: "IO delay", color: "#e5a94c", values: col(rrd.rows, (r) => (r.iowait != null ? r.iowait * 100 : undefined)) },
              ]} />
          </Card>
          <Card>
            <MetricChart title="Server load" times={times} loading={rrd.loading} format={(v) => v.toFixed(1)}
              series={[{ label: "Load average", color: "#4f8cff", area: true, values: col(rrd.rows, (r) => r.loadavg) }]} />
          </Card>
          <Card>
            <MetricChart title="Memory usage" times={times} loading={rrd.loading} format={(v) => formatBytes(v)} fixedMax={memTotal || undefined}
              series={[
                { label: "Used", color: "#4f8cff", area: true, values: col(rrd.rows, (r) => r.memused) },
                ...(hasArc ? [{ label: "ZFS ARC", color: "#2fbf71", values: col(rrd.rows, (r) => r.arcsize) }] : []),
              ]} />
          </Card>
          <Card>
            <MetricChart title="Network traffic" times={times} loading={rrd.loading} format={rate}
              series={[
                { label: "In", color: "#2fbf71", area: true, values: col(rrd.rows, (r) => r.netin) },
                { label: "Out", color: "#4f8cff", values: col(rrd.rows, (r) => r.netout) },
              ]} />
          </Card>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-4">
        <Card>
          <CardTitle>System</CardTitle>
          <dl>
            <Row label="CPU" value={s?.cpu_model ? `${s.cpu_model}` : "—"} />
            <Row label="Sockets / cores / threads" value={s ? `${s.cpu_sockets ?? "?"} / ${s.cpu_cores ?? "?"} per socket / ${s.cpu_threads ?? "?"}` : "—"} />
            <Row label="Memory total" value={formatBytes(s?.mem_total ?? node.mem_total_bytes)} />
            <Row label="Allocated to workloads" value={node.allocated_memory_bytes != null ? `${formatBytes(node.allocated_memory_bytes)}${(s?.mem_total ?? node.mem_total_bytes) ? ` (${Math.round((node.allocated_memory_bytes / ((s?.mem_total ?? node.mem_total_bytes) as number)) * 100)}% of RAM)` : ""}` : "—"} />
            <Row label="Kernel" value={s?.kernel ?? node.kernel_version ?? "—"} />
            <Row label="Boot mode" value={s?.boot_mode ? `${s.boot_mode.toUpperCase()}${s.secure_boot != null ? (s.secure_boot ? ", Secure Boot on" : ", Secure Boot off") : ""}` : "—"} />
            <Row label="KSM sharing" value={s?.ksm_shared != null ? formatBytes(s.ksm_shared) : "—"} />
            <PendingUpdatesRow nodeId={nodeId} initialCount={pending} />
            <Row label="Last seen" value={new Date(node.last_seen).toLocaleString()} />
          </dl>
        </Card>
        <Card>
          <CardTitle>Storage on this host</CardTitle>
          {live == null ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : live.storage.length === 0 ? (
            <div className="text-sm text-muted">No storage reported.</div>
          ) : (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Name</th><th className="pr-3">Type</th><th className="pr-3">Used</th><th className="text-right">Size</th></tr></thead>
              <tbody>
                {live.storage.map((st) => (
                  <tr key={st.name} className="border-t border-border">
                    <td className="py-1.5 pr-3">{st.name}{st.shared ? <span className="text-muted text-xs ml-1">(shared)</span> : null}{!st.active ? <span className="text-warn text-xs ml-1">(inactive)</span> : null}</td>
                    <td className="pr-3 text-muted">{st.type || "—"}</td>
                    <td className="pr-3">
                      {st.used_pct != null ? (
                        <span className="inline-flex items-center gap-2">
                          <span className="inline-block h-1.5 w-16 rounded-full bg-border overflow-hidden"><span className="block h-full rounded-full" style={{ width: `${Math.min(st.used_pct, 100)}%`, backgroundColor: barColor(st.used_pct) }} /></span>
                          <span className="text-xs text-muted">{st.used_pct.toFixed(0)}%</span>
                        </span>
                      ) : "—"}
                    </td>
                    <td className="text-right text-muted">{formatBytes(st.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card className="mb-4">
        <CardTitle>VM responsiveness</CardTitle>
        {responsiveness == null ? (
          <div className="text-sm text-muted">Checking the running VMs on this host…</div>
        ) : responsiveness.error ? (
          <div className="text-sm text-warn">Could not check: {responsiveness.error}</div>
        ) : (
          (() => {
            const attention = responsiveness.vms.filter((v) => v.state !== "ok" && v.state !== "stopped");
            const slowest = Math.max(0, ...responsiveness.vms.map((v) => v.elapsed));
            if (attention.length === 0) {
              return (
                <div className="text-sm">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium mr-2 ${LIVENESS_CLASS.ok}`}>OK</span>
                  All {responsiveness.checked} running VMs answered normally (slowest {slowest.toFixed(2)} s).
                </div>
              );
            }
            return (
              <div className="text-sm">
                <div className="text-bad mb-2">
                  {attention.length} of {responsiveness.checked} running VMs {attention.length === 1 ? "is" : "are"} not answering normally. A live migration of {attention.length === 1 ? "it" : "them"} would hang; power-cycle from PVE first.
                </div>
                <ul className="space-y-1">
                  {attention.map((v) => (
                    <li key={v.workload_id} className="flex items-center gap-2">
                      <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${LIVENESS_CLASS[v.state]}`}>{LIVENESS_LABEL[v.state]}</span>
                      <Link href={`/infrastructure/workloads/${v.workload_id}`} className="text-accent hover:underline">{v.name || `VM ${v.vmid}`}</Link>
                      <span className="text-muted text-xs">{v.detail}</span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })()
        )}
      </Card>
    </div>
  );
}
