"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { Node, StorageItem, Workload } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { Meter } from "@/components/Gauges";
import MetricChart from "@/components/MetricChart";
import WorkloadLifecycleButtons from "@/components/WorkloadLifecycleButtons";
import MigrateWorkloadAction from "@/components/MigrateWorkloadAction";
import { PreferredHostSelect, ProfileSelect, StoragePreferenceSelect } from "@/components/ProfileSelect";
import { formatBytes, formatUptime, formatRelativeTime } from "@/lib/format";
import { onOperationsChanged } from "@/lib/operationsBus";
import {
  TIMEFRAMES,
  type ConfigSummary,
  type RrdRow,
  type Timeframe,
  type WorkloadLive,
  type WorkloadOverview,
  type WorkloadTask,
} from "@/lib/workloadDetail";

export type TabId = "summary" | "statistics" | "hardware" | "tasks" | "console";
const TABS: { id: TabId; label: string }[] = [
  { id: "summary", label: "Summary" },
  { id: "statistics", label: "Statistics" },
  { id: "hardware", label: "Hardware" },
  { id: "tasks", label: "Tasks" },
  { id: "console", label: "Console" },
];

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-0.5 text-sm">
      <dt className="text-muted shrink-0">{label}</dt>
      <dd className="text-text text-right min-w-0 break-words">{value}</dd>
    </div>
  );
}

function CheckBadge({ check }: { check: WorkloadLive["check"] }) {
  if (!check) return null;
  const label = { ok: "Live check OK", warn: "Live check: slow", bad: "Not responding", info: "Live check: changed", unknown: "Live check: no answer" }[check.state];
  const cls = { ok: "bg-good/15 text-good", warn: "bg-warn/15 text-warn", bad: "bg-bad/15 text-bad", info: "bg-accent/15 text-accent", unknown: "bg-muted/15 text-muted" }[check.state];
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`} title={check.detail}>
      {label}
    </span>
  );
}

/** Polls the live PVE read. Pauses while the tab is hidden. */
function useLive(workloadId: string): { live: WorkloadLive | null; refresh: () => void } {
  const [live, setLive] = useState<WorkloadLive | null>(null);
  const refresh = useCallback(() => {
    fetch(`/api/workloads/${workloadId}/live`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: WorkloadLive | null) => d && setLive(d))
      .catch(() => {});
  }, [workloadId]);
  useEffect(() => {
    refresh();
    const iv = setInterval(() => {
      if (!document.hidden) refresh();
    }, 10000);
    const off = onOperationsChanged(refresh);
    return () => {
      clearInterval(iv);
      off();
    };
  }, [refresh]);
  return { live, refresh };
}

function useRrd(workloadId: string, timeframe: Timeframe, enabled: boolean): { rows: RrdRow[]; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ rows: RrdRow[]; error: string | null; loading: boolean }>({ rows: [], error: null, loading: true });
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setState({ rows: [], error: null, loading: true });
    const load = () =>
      fetch(`/api/workloads/${workloadId}/rrd?timeframe=${timeframe}`)
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
  }, [workloadId, timeframe, enabled]);
  return state;
}

/** Tasks arrive pre-loaded on the full page; a table row fetches them the first time its Tasks tab opens. */
function useTasks(workloadId: string, enabled: boolean, initial?: WorkloadTask[]): { rows: WorkloadTask[] | null } {
  const [rows, setRows] = useState<WorkloadTask[] | null>(initial ?? null);
  useEffect(() => {
    if (!enabled || initial !== undefined) return;
    let cancelled = false;
    fetch(`/api/workloads/${workloadId}/tasks`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => !cancelled && setRows(d))
      .catch(() => !cancelled && setRows([]));
    return () => {
      cancelled = true;
    };
  }, [workloadId, enabled, initial]);
  return { rows };
}

const pct = (v: number) => `${v < 10 ? v.toFixed(1) : v.toFixed(0)}%`;
const rate = (v: number) => `${formatBytes(v)}/s`;
const col = (rows: RrdRow[], f: (r: RrdRow) => number | undefined) => rows.map((r) => f(r) ?? null);

function Charts({ rows, which, loading }: { rows: RrdRow[]; which: "summary" | "all"; loading: boolean }) {
  const times = rows.map((r) => r.time as number);
  const maxmem = Math.max(0, ...rows.map((r) => r.maxmem ?? 0));
  const cpu = (
    <MetricChart key="cpu" title="CPU usage" times={times} format={pct} loading={loading}
      series={[{ label: "CPU", color: "#4f8cff", area: true, values: col(rows, (r) => (r.cpu != null ? r.cpu * 100 : undefined)) }]} />
  );
  const mem = (
    <MetricChart key="mem" title="Memory usage" times={times} format={(v) => formatBytes(v)} fixedMax={maxmem || undefined} loading={loading}
      series={[
        { label: "Used", color: "#4f8cff", area: true, values: col(rows, (r) => r.mem) },
        { label: "Host", color: "#8b95a7", values: col(rows, (r) => r.memhost) },
      ]} />
  );
  if (which === "summary") return <div className="space-y-4">{cpu}{mem}</div>;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <Card>{cpu}</Card>
      <Card>{mem}</Card>
      <Card>
        <MetricChart title="Network traffic" times={times} loading={loading} format={rate}
          series={[
            { label: "In", color: "#2fbf71", area: true, values: col(rows, (r) => r.netin) },
            { label: "Out", color: "#4f8cff", values: col(rows, (r) => r.netout) },
          ]} />
      </Card>
      <Card>
        <MetricChart title="Disk IO" times={times} loading={loading} format={rate}
          series={[
            { label: "Read", color: "#2fbf71", area: true, values: col(rows, (r) => r.diskread) },
            { label: "Write", color: "#4f8cff", values: col(rows, (r) => r.diskwrite) },
          ]} />
      </Card>
      <Card>
        <MetricChart title="CPU pressure stall" times={times} loading={loading} format={(v) => `${v.toFixed(2)}%`}
          series={[
            { label: "Some", color: "#e5a94c", area: true, values: col(rows, (r) => r.pressurecpusome) },
            { label: "Full", color: "#e5484d", values: col(rows, (r) => r.pressurecpufull) },
          ]} />
      </Card>
      <Card>
        <MetricChart title="IO pressure stall" times={times} loading={loading} format={(v) => `${v.toFixed(2)}%`}
          series={[
            { label: "Some", color: "#e5a94c", area: true, values: col(rows, (r) => r.pressureiosome) },
            { label: "Full", color: "#e5484d", values: col(rows, (r) => r.pressureiofull) },
          ]} />
      </Card>
    </div>
  );
}

function IpRow({ workloadId, running }: { workloadId: string; running: boolean }) {
  const [ips, setIps] = useState<string[] | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    fetch(`/api/workloads/${workloadId}/ips`)
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        if (d.error) setErr(true);
        setIps((d.addresses || []).map((a: { address: string }) => a.address));
      })
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [workloadId, running]);
  let value: React.ReactNode = "—";
  if (running) value = ips === null ? <span className="text-muted">checking…</span> : ips.length ? ips.join(", ") : err ? <span className="text-muted" title="The guest agent did not answer">no agent answer</span> : "—";
  return <Row label="IP addresses" value={value} />;
}

type ProfileValues = {
  sensitivity: string;
  downtime_tolerance: string;
  storage_preference: string | null;
  preferred_node_id: string | null;
};
type Profile = { values: ProfileValues; onChange: (patch: Partial<ProfileValues>) => void };


type PanelFinding = { id: string; severity: string; title: string };

/** The Summary / Statistics / Hardware / Tasks / Console tabs. Used both on the full page
 * and inside an expanded Workloads table row, so the two never drift apart. Data for a tab
 * is only fetched while that tab is showing. */
export function WorkloadDetailPanel({
  workload: w,
  findings,
  live,
  tab,
  onTabChange,
  initialTasks,
  profile,
  clusterNodes,
}: {
  workload: Workload;
  findings: PanelFinding[];
  live: WorkloadLive | null;
  tab: TabId;
  onTabChange: (t: TabId) => void;
  /** Provided by the full page (server-rendered); the table row loads them on demand. */
  initialTasks?: WorkloadTask[];
  /** Provided by the table so its selects and this panel stay in step. */
  profile?: Profile;
  /** Hosts in this workload's cluster, for the Preferred host choice. */
  clusterNodes: Node[];
}) {
  const [timeframe, setTimeframe] = useState<Timeframe>("hour");
  const [localProfile, setLocalProfile] = useState<ProfileValues>({
    sensitivity: w.sensitivity,
    downtime_tolerance: w.downtime_tolerance,
    storage_preference: w.storage_preference,
    preferred_node_id: w.preferred_node_id,
  });
  const pv = profile ? profile.values : localProfile;
  const changeProfile = (patch: Partial<ProfileValues>) => (profile ? profile.onChange(patch) : setLocalProfile((p) => ({ ...p, ...patch })));
  const lxc = w.type === "lxc";
  const cfg: ConfigSummary | null = live?.config ?? null;
  const st = live?.status ?? null;
  const status = st?.status ?? w.status;
  const rrdSummary = useRrd(w.id, "hour", tab === "summary");
  const rrdStats = useRrd(w.id, timeframe, tab === "statistics");
  const tasks = useTasks(w.id, tab === "tasks", initialTasks);

  return (
    <div>
      {live?.error && (
        <div className="mb-3 px-3 py-2 rounded border border-warn/40 bg-warn/10 text-warn text-xs">Live PVE read incomplete: {live.error}</div>
      )}

      <div className="flex gap-1 border-b border-border mb-4">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => onTabChange(t.id)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 ${tab === t.id ? "border-accent text-accent" : "border-transparent text-muted hover:text-text"}`}
          >
            {t.label}
            {t.id === "tasks" && tasks.rows && tasks.rows.length > 0 ? <span className="ml-1 text-xs text-muted">{tasks.rows.length}</span> : null}
          </button>
        ))}
      </div>

      {tab === "summary" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          <div className="space-y-4">
            <Card>
              <CardTitle>Status</CardTitle>
              <dl>
                <Row label="Status" value={<StatusBadge status={w.is_missing ? "unknown" : status} />} />
                <Row label="HA state" value={st?.ha?.managed ? st.ha.state || "managed" : w.ha_state || "none"} />
                <Row label="Start at boot" value={cfg ? (cfg.start_at_boot ? "Yes" : "No") : "—"} />
                {!lxc && <Row label="Guest agent" value={cfg ? (cfg.agent_enabled ? "Enabled" : "Not enabled") : "—"} />}
                <Row label="Uptime" value={formatUptime(st?.uptime)} />
                <IpRow workloadId={w.id} running={status === "running" && !w.is_missing} />
                <Row label="Tags" value={(cfg?.tags?.length ? cfg.tags : w.tags || []).join(", ") || "—"} />
                {(st?.lock || cfg?.lock) && <Row label="Lock" value={<span className="text-warn">{st?.lock || cfg?.lock}</span>} />}
                {cfg?.protection && <Row label="Protection" value="On (cannot be deleted)" />}
                {st?.["running-qemu"] && <Row label="QEMU / machine" value={`${st["running-qemu"]} / ${st["running-machine"] ?? "—"}`} />}
              </dl>
              {st && status === "running" && (
                <div className="mt-3 space-y-2 text-sm">
                  <div className="flex items-center justify-between"><span className="text-muted">CPU</span><span className="flex items-center gap-2"><Meter value={(st.cpu ?? 0) * 100} width={160} /> <span className="text-muted text-xs">of {st.cpus ?? w.cpu_cores ?? "?"} vCPU</span></span></div>
                  <div className="flex items-center justify-between"><span className="text-muted">RAM</span><span className="flex items-center gap-2"><Meter value={st.maxmem ? ((st.mem ?? 0) / st.maxmem) * 100 : null} width={160} /> <span className="text-muted text-xs">{formatBytes(st.mem)} of {formatBytes(st.maxmem)}</span></span></div>
                </div>
              )}
            </Card>
            {cfg && (
              <Card>
                <div className="flex items-center justify-between"><CardTitle>Hardware</CardTitle><button type="button" onClick={() => onTabChange("hardware")} className="text-xs text-accent hover:underline">Details</button></div>
                <dl>
                  <Row label="CPU / RAM" value={`${cfg.vcpus ?? "—"} vCPU · ${cfg.memory_mb ? formatBytes(cfg.memory_mb * 1048576) : "—"}`} />
                  <Row label="Disks" value={cfg.disks.filter((d) => d.kind === "disk").map((d) => `${d.size ?? "?"} on ${d.storage ?? "?"}`).join(", ") || "—"} />
                  <Row label="Network" value={cfg.nets.map((n) => `${n.bridge ?? "?"}${n.vlan_tag ? ` (VLAN ${n.vlan_tag})` : ""}`).join(", ") || "—"} />
                  {!lxc && <Row label="BIOS" value={cfg.bios || "—"} />}
                </dl>
              </Card>
            )}
            <Card>
              <CardTitle>PyXie placement profile</CardTitle>
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between" title="'restricted' means this workload is never placed on a node tagged Trust Tier = low."><span className="text-muted">Sensitivity</span><ProfileSelect workloadId={w.id} field="sensitivity" value={pv.sensitivity} options={["standard", "restricted"]} highlightWhen="restricted" onChanged={(v) => changeProfile({ sensitivity: v })} /></div>
                <div className="flex items-center justify-between" title="How much outage this workload can absorb; 'low' favours the best, most-trusted nodes."><span className="text-muted">Downtime tolerance</span><ProfileSelect workloadId={w.id} field="downtime_tolerance" value={pv.downtime_tolerance} options={["low", "standard", "high"]} highlightWhen="low" onChanged={(v) => changeProfile({ downtime_tolerance: v })} /></div>
                <div className="flex items-center justify-between" title="Where this workload's disk goes when migrated. 'auto' follows where it lives today."><span className="text-muted">Storage preference</span><StoragePreferenceSelect workloadId={w.id} value={pv.storage_preference} onChanged={(v) => changeProfile({ storage_preference: v })} /></div>
                <div className="flex items-center justify-between" title="A soft preference for which node this workload lives on; never overrides a hard block."><span className="text-muted">Preferred host</span><PreferredHostSelect workloadId={w.id} clusterNodes={clusterNodes} value={pv.preferred_node_id} onChanged={(v) => changeProfile({ preferred_node_id: v })} /></div>
                {w.placement_notes && <div className="text-muted whitespace-pre-wrap pt-1">{w.placement_notes}</div>}
              </div>
            </Card>
            {findings.length > 0 && (
              <Card>
                <CardTitle>Needs attention</CardTitle>
                <ul className="text-sm space-y-1">
                  {findings.map((f) => (
                    <li key={f.id} className="flex items-center gap-2"><StatusBadge status={f.severity === "info" ? "unknown" : f.severity} /><span>{f.title}</span></li>
                  ))}
                </ul>
              </Card>
            )}
            {cfg?.description && (
              <Card><CardTitle>Notes (from PVE)</CardTitle><div className="text-sm whitespace-pre-wrap text-text">{cfg.description}</div></Card>
            )}
          </div>
          <Card>
            <div className="flex items-center justify-between mb-2">
              <CardTitle>Last hour</CardTitle>
              <button type="button" onClick={() => onTabChange("statistics")} className="text-xs text-accent hover:underline">All statistics</button>
            </div>
            {rrdSummary.error ? <div className="text-sm text-warn">Could not load PVE graphs: {rrdSummary.error}</div> : <Charts rows={rrdSummary.rows} which="summary" loading={rrdSummary.loading} />}
          </Card>
        </div>
      )}

      {tab === "statistics" && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs text-muted">Graphs are PVE's own rrd series for this guest, read live.</div>
            <label className="text-sm text-muted flex items-center gap-2">
              Timeframe
              <select className="bg-surface2 border border-border rounded px-2 py-1 text-sm text-text" value={timeframe} onChange={(e) => setTimeframe(e.target.value as Timeframe)}>
                {TIMEFRAMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </label>
          </div>
          {rrdStats.error ? <div className="text-sm text-warn">Could not load PVE graphs: {rrdStats.error}</div> : <Charts rows={rrdStats.rows} which="all" loading={rrdStats.loading} />}
        </div>
      )}

      {tab === "hardware" && <Hardware cfg={cfg} lxc={lxc} loading={live === null} />}

      {tab === "tasks" && (
        <Card>
          <CardTitle>PVE tasks for this guest</CardTitle>
          {tasks.rows === null ? <div className="text-sm text-muted">Loading…</div> : tasks.rows.length === 0 ? <div className="text-sm text-muted">No recent tasks recorded.</div> : (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Task</th><th className="pr-3">Result</th><th className="pr-3">Started</th><th className="pr-3">Finished</th><th>By</th></tr></thead>
              <tbody>
                {tasks.rows.map((t) => (
                  <tr key={t.id} className="border-t border-border">
                    <td className="py-1.5 pr-3">{t.task_type || "—"}</td>
                    <td className="pr-3"><StatusBadge status={t.status === "running" ? "running" : t.exit_status && t.exit_status !== "OK" ? "failure" : t.status || "unknown"} /></td>
                    <td className="pr-3 text-muted" title={t.started_at ? new Date(t.started_at).toLocaleString() : ""}>{formatRelativeTime(t.started_at)}</td>
                    <td className="pr-3 text-muted">{t.ended_at ? formatRelativeTime(t.ended_at) : "—"}</td>
                    <td className="text-muted">{t.user || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {tab === "console" && (
        <Card>
          <CardTitle>Console</CardTitle>
          <div className="text-sm text-text space-y-2">
            <p>The console is planned for this tab, opening the guest's screen in the browser.</p>
            <p className="text-muted">It is not available yet: PyXie's PVE account has no console permission (<code>VM.Console</code>), and granting it is a separate decision. Until then, open the guest's console from the PVE web interface.</p>
          </div>
        </Card>
      )}
    </div>
  );
}

/** What an expanded Workloads table row shows. Mounted only while the row is open, so the
 * live polling stops the moment it is collapsed. */
export function WorkloadRowDetail({ workload, findings, profile, clusterNodes }: { workload: Workload; findings: PanelFinding[]; profile?: Profile; clusterNodes: Node[] }) {
  const [tab, setTab] = useState<TabId>("summary");
  const { live } = useLive(workload.id);
  return (
    <div>
      <div className="flex items-center justify-between mb-3 text-sm">
        <div className="flex items-center gap-2">
          {!workload.is_missing && <CheckBadge check={live?.check ?? null} />}
          {live?.status?.uptime ? <span className="text-muted">up {formatUptime(live.status.uptime)}</span> : null}
        </div>
        <div className="flex items-center gap-3">
          <Link href={`/operations/maintenance?workload=${workload.id}`} className="text-muted hover:text-text">Maintenance</Link>
          <Link href={`/infrastructure/workloads/${workload.id}`} className="text-accent hover:underline">Open full page ↗</Link>
        </div>
      </div>
      <WorkloadDetailPanel workload={workload} findings={findings} live={live} tab={tab} onTabChange={setTab} profile={profile} clusterNodes={clusterNodes} />
    </div>
  );
}

export default function WorkloadDetail({
  overview,
  tasks,
  nodes,
  storage,
}: {
  overview: WorkloadOverview;
  tasks: WorkloadTask[];
  nodes: Node[];
  storage: StorageItem[];
}) {
  const router = useRouter();
  const search = useSearchParams();
  const tabParam = search.get("tab") as TabId | null;
  const tab: TabId = TABS.some((t) => t.id === tabParam) ? (tabParam as TabId) : "summary";
  const { live } = useLive(overview.workload.id);
  const w = overview.workload;
  const lxc = w.type === "lxc";
  const st = live?.status ?? null;
  const status = st?.status ?? w.status;
  const name = w.name || `VMID ${w.vmid}`;

  function setTab(id: TabId) {
    router.replace(`/infrastructure/workloads/${w.id}${id === "summary" ? "" : `?tab=${id}`}`, { scroll: false });
  }

  return (
    <div>
      {overview.node.maintenance_mode && (
        <div className="mb-3 px-3 py-2 rounded border border-warn/40 bg-warn/10 text-warn text-sm">
          Its host {overview.node.name} is in maintenance mode.
        </div>
      )}

      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold text-text">{name}</h1>
            <StatusBadge status={w.is_missing ? "unknown" : status} />
            {!w.is_missing && <CheckBadge check={live?.check ?? null} />}
            {w.is_missing && <span className="text-xs text-warn">Not currently visible to PVE</span>}
          </div>
          <div className="text-sm text-muted mt-1">
            {lxc ? "Container" : "VM"} {w.vmid} ·{" "}
            <Link href={`/infrastructure/nodes/${overview.node.id}`} className="text-accent hover:underline">{overview.node.name}</Link>
            {" · "}Cluster {overview.cluster.name}
            {st?.uptime ? ` · uptime ${formatUptime(st.uptime)}` : ""}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <WorkloadLifecycleButtons
            workloadId={w.id}
            workloadName={name}
            status={status}
            disabled={lxc || w.is_missing}
            disabledReason={w.is_missing ? "Not currently visible to PVE" : "Guest lifecycle actions are only implemented for VMs, not containers"}
          />
          {!lxc && !w.is_missing && (
            <MigrateWorkloadAction workloadId={w.id} workloadName={name} clusterId={w.cluster_id} currentNodeId={w.node_id} nodes={nodes} storage={storage} />
          )}
          <Link href={`/operations/maintenance?workload=${w.id}`} className="px-2.5 py-1 rounded border border-border text-sm text-muted hover:text-text">
            Maintenance
          </Link>
          <button type="button" disabled className="px-2.5 py-1 rounded border border-border text-sm text-muted opacity-50 cursor-not-allowed" title="Console is not available yet — it needs a PVE permission change first (see the Console tab).">
            Console
          </button>
        </div>
      </div>

      <WorkloadDetailPanel workload={w} findings={overview.findings} live={live} tab={tab} onTabChange={setTab} initialTasks={tasks} clusterNodes={nodes.filter((n) => n.cluster_id === w.cluster_id)} />
    </div>
  );
}

function Hardware({ cfg, lxc, loading }: { cfg: ConfigSummary | null; lxc: boolean; loading: boolean }) {
  if (!cfg) {
    return (
      <Card>
        <div className="text-sm text-muted">
          {loading ? "Loading hardware details…" : "Hardware details come from PVE and could not be read right now."}
        </div>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>Compute</CardTitle>
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8">
          <Row label="vCPUs" value={cfg.vcpus ? `${cfg.vcpus} (${cfg.sockets} socket × ${cfg.cores ?? "?"} cores)` : "—"} />
          <Row label="Memory" value={cfg.memory_mb ? `${formatBytes(cfg.memory_mb * 1048576)}` : "—"} />
          {!lxc && <Row label="CPU type" value={cfg.cpu_type || "default"} />}
          {!lxc && <Row label="Ballooning minimum" value={cfg.balloon_min_mb === 0 ? "Off" : cfg.balloon_min_mb ? formatBytes(cfg.balloon_min_mb * 1048576) : "Not set"} />}
          {!lxc && <Row label="BIOS" value={cfg.bios || "—"} />}
          {!lxc && <Row label="Machine" value={cfg.machine || "default"} />}
          {!lxc && <Row label="SCSI controller" value={cfg.scsihw || "—"} />}
          {!lxc && <Row label="Boot order" value={cfg.boot || "—"} />}
          <Row label="OS type" value={cfg.ostype || "—"} />
          {lxc && <Row label="Swap" value={cfg.swap_mb != null ? formatBytes(cfg.swap_mb * 1048576) : "—"} />}
          {lxc && <Row label="Unprivileged" value={cfg.unprivileged ? "Yes" : "No"} />}
        </dl>
      </Card>
      <Card>
        <CardTitle>Disks</CardTitle>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Device</th><th className="pr-3">Storage</th><th className="pr-3">Size</th><th>Volume and options</th></tr></thead>
          <tbody>
            {cfg.disks.map((d) => (
              <tr key={d.key} className="border-t border-border">
                <td className="py-1.5 pr-3">{d.key}{d.kind !== "disk" ? <span className="text-muted text-xs ml-1">({d.kind})</span> : null}</td>
                <td className="pr-3">{d.storage || "—"}</td>
                <td className="pr-3">{d.empty ? "empty" : d.size || "—"}</td>
                <td className="text-muted text-xs break-all">{d.empty ? "" : `${d.volume}${Object.keys(d.options).length ? " · " + Object.entries(d.options).map(([k, v]) => `${k}=${v}`).join(", ") : ""}`}</td>
              </tr>
            ))}
            {cfg.disks.length === 0 && <tr><td colSpan={4} className="text-muted py-2">No disks.</td></tr>}
          </tbody>
        </table>
      </Card>
      <Card>
        <CardTitle>Network</CardTitle>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Device</th><th className="pr-3">Bridge</th><th className="pr-3">VLAN</th><th className="pr-3">MAC</th><th>{lxc ? "IP" : "Model"}</th></tr></thead>
          <tbody>
            {cfg.nets.map((n) => (
              <tr key={n.key} className="border-t border-border">
                <td className="py-1.5 pr-3">{n.key}{n.firewall ? <span className="text-muted text-xs ml-1">(firewall)</span> : null}</td>
                <td className="pr-3">{n.bridge || "—"}</td>
                <td className="pr-3">{n.vlan_tag || "—"}</td>
                <td className="pr-3 text-muted">{n.mac || "—"}</td>
                <td>{(lxc ? n.ip : n.model) || "—"}</td>
              </tr>
            ))}
            {cfg.nets.length === 0 && <tr><td colSpan={5} className="text-muted py-2">No network devices.</td></tr>}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
