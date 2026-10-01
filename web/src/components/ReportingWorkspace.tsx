"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Table } from "@/components/Table";
import type { Column } from "@/components/Table";
import ReportMultiSelect from "@/components/ReportMultiSelect";
import { useMe } from "@/lib/useMe";

type ColMeta = { key: string; label: string; kind: string; hidden: boolean };
type Catalog = { key: string; label: string; description: string; columns: ColMeta[] }[];
type Options = {
  clusters: { id: string; name: string }[];
  nodes: { id: string; name: string; cluster_id: string }[];
  workloads: { id: string; name: string; vmid: number; type: string; node_id: string; cluster_id: string }[];
};
type Status = {
  running: boolean;
  collected_at: string | null;
  last_run: {
    status: string;
    started_at: string;
    ended_at: string | null;
    triggered_by: string;
    workloads_total: number;
    workloads_collected: number;
    workloads_failed: number;
    error: string | null;
  } | null;
};
type ReportData = { key: string; columns: ColMeta[]; rows: Row[]; collected_at: string | null };
type Row = { id: string; [k: string]: unknown };

function fmt(c: ColMeta, v: unknown): React.ReactNode {
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  if (c.kind === "bool") return v ? "Yes" : "No";
  if (c.kind === "datetime") return new Date(String(v)).toLocaleString();
  if (c.kind === "num") return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (c.kind === "int") return Number(v).toLocaleString();
  return String(v);
}

function sortVal(c: ColMeta, v: unknown): string | number | null {
  if (v === null || v === undefined || v === "") return null;
  if (c.kind === "bool") return v ? 1 : 0;
  if (c.kind === "datetime") return Date.parse(String(v));
  if (c.kind === "num" || c.kind === "int") return Number(v);
  return String(v).toLowerCase();
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export default function ReportingWorkspace({
  catalog,
  options,
  initialStatus,
}: {
  catalog: Catalog;
  options: Options;
  initialStatus: Status | null;
}) {
  const me = useMe();
  const [active, setActive] = useState(catalog[0]?.key ?? "vInfo");
  const [clusters, setClusters] = useState<Set<string>>(new Set());
  const [nodes, setNodes] = useState<Set<string>>(new Set());
  const [vms, setVms] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Status | null>(initialStatus);
  const [exportOpen, setExportOpen] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const reqId = useRef(0);

  // Host / VM pickers only offer what the wider selection could still match.
  const nodeOptions = useMemo(
    () =>
      options.nodes
        .filter((n) => clusters.size === 0 || clusters.has(n.cluster_id))
        .map((n) => ({ id: n.id, label: n.name, sub: options.clusters.find((c) => c.id === n.cluster_id)?.name })),
    [options, clusters]
  );
  const vmOptions = useMemo(
    () =>
      options.workloads
        .filter((w) => (clusters.size === 0 || clusters.has(w.cluster_id)) && (nodes.size === 0 || nodes.has(w.node_id)))
        .map((w) => ({ id: w.id, label: w.name, sub: `${w.type === "vm" ? "VM" : "CT"} ${w.vmid}` })),
    [options, clusters, nodes]
  );

  // Drop selections that a wider filter has since made unreachable.
  useEffect(() => {
    const ok = new Set(nodeOptions.map((o) => o.id));
    setNodes((prev) => (Array.from(prev).every((i) => ok.has(i)) ? prev : new Set(Array.from(prev).filter((i) => ok.has(i)))));
  }, [nodeOptions]);
  useEffect(() => {
    const ok = new Set(vmOptions.map((o) => o.id));
    setVms((prev) => (Array.from(prev).every((i) => ok.has(i)) ? prev : new Set(Array.from(prev).filter((i) => ok.has(i)))));
  }, [vmOptions]);

  const scopeQs = useMemo(() => {
    const p = new URLSearchParams();
    if (clusters.size) p.set("cluster_id", Array.from(clusters).join(","));
    if (nodes.size) p.set("node_id", Array.from(nodes).join(","));
    if (vms.size) p.set("workload_id", Array.from(vms).join(","));
    return p;
  }, [clusters, nodes, vms]);

  const load = useCallback(async () => {
    const id = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/reports/${active}?${scopeQs.toString()}`);
      const body = await res.json().catch(() => ({}));
      if (id !== reqId.current) return; // a newer request superseded this one
      if (!res.ok) {
        setError(body.error || `Request failed (${res.status})`);
        setData(null);
      } else {
        setData(body);
      }
    } catch (e) {
      if (id === reqId.current) setError((e as Error).message);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [active, scopeQs]);

  useEffect(() => {
    load();
  }, [load]);

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/reports/status");
      if (res.ok) setStatus(await res.json());
    } catch {
      /* status line is best-effort */
    }
  }, []);

  // While a collection is running, poll until it finishes, then reload the table.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (status?.running) {
      wasRunning.current = true;
      const t = setInterval(refreshStatus, 3000);
      return () => clearInterval(t);
    }
    if (wasRunning.current) {
      wasRunning.current = false;
      load();
    }
  }, [status?.running, refreshStatus, load]);

  async function refreshNow() {
    setRefreshError(null);
    try {
      const res = await fetch("/api/reports/refresh", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setRefreshError(body.error || `Request failed (${res.status})`);
        return;
      }
      setStatus((s) => ({ ...(s ?? { collected_at: null, last_run: null }), running: true }));
    } catch (e) {
      setRefreshError((e as Error).message);
    }
  }

  function download(reports: string[], format: "xlsx" | "csv") {
    const p = new URLSearchParams(scopeQs);
    p.set("reports", reports.join(","));
    p.set("format", format);
    setExportOpen(false);
    window.location.assign(`/api/reports/export?${p.toString()}`);
  }

  const columns: Column<Row>[] = useMemo(
    () =>
      (data?.columns ?? []).map((c) => ({
        header: c.label,
        optional: true,
        defaultHidden: c.hidden,
        className: c.kind === "int" || c.kind === "num" ? "text-right tabular-nums" : undefined,
        render: (r: Row) => fmt(c, r[c.key]),
        sortValue: (r: Row) => sortVal(c, r[c.key]),
      })),
    [data?.columns]
  );

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((r) => Object.entries(r).some(([k, v]) => k !== "id" && v != null && String(v).toLowerCase().includes(q)));
  }, [data?.rows, search]);

  const allKeys = catalog.map((c) => c.key);
  const isAdmin = me?.is_admin === true;
  const last = status?.last_run;
  const scoped = clusters.size + nodes.size + vms.size > 0;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <ReportMultiSelect label="Clusters" options={options.clusters.map((c) => ({ id: c.id, label: c.name }))} selected={clusters} onChange={setClusters} />
        <ReportMultiSelect label="Hosts" options={nodeOptions} selected={nodes} onChange={setNodes} />
        <ReportMultiSelect label="VMs" options={vmOptions} selected={vms} onChange={setVms} />
        {scoped && (
          <button
            className="text-xs text-muted hover:text-text"
            onClick={() => {
              setClusters(new Set());
              setNodes(new Set());
              setVms(new Set());
            }}
          >
            Reset scope
          </button>
        )}
        <div className="ml-auto flex items-center gap-2 relative">
          {isAdmin && (
            <button
              onClick={refreshNow}
              disabled={status?.running}
              className="text-sm px-3 py-1.5 rounded border border-border bg-surface2 hover:bg-surface text-text disabled:opacity-50"
              title="Re-collect disks, NICs, snapshots and guest IPs from PVE now"
            >
              {status?.running ? "Collecting…" : "Refresh now"}
            </button>
          )}
          <button
            onClick={() => setExportOpen((o) => !o)}
            className="text-sm px-3 py-1.5 rounded border border-accent text-accent bg-accent/10 hover:bg-accent/20"
          >
            Export ▾
          </button>
          {exportOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setExportOpen(false)} />
              <div className="absolute right-0 top-10 z-20 w-64 bg-surface2 border border-border rounded-lg shadow-lg py-1 text-sm">
                <ExportItem label={`Excel — ${active}`} onClick={() => download([active], "xlsx")} />
                <ExportItem label="Excel — all reports (one tab each)" onClick={() => download(allKeys, "xlsx")} />
                <div className="border-t border-border my-1" />
                <ExportItem label={`CSV — ${active}`} onClick={() => download([active], "csv")} />
                <ExportItem label="CSV — all reports (zip)" onClick={() => download(allKeys, "csv")} />
                <div className="px-3 pt-1 pb-1.5 text-xs text-muted">Exports honour the scope above and include every column.</div>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted mb-3">
        <span>
          Disks, NICs, snapshots &amp; guest IPs collected {ago(status?.collected_at ?? null)}
          {status?.collected_at && <> ({new Date(status.collected_at).toLocaleString()})</>}
        </span>
        {last && last.status === "partial" && (
          <span className="text-warn">
            {last.workloads_failed} of {last.workloads_total} guests could not be read last pass — their previous details are kept.
          </span>
        )}
        {last && last.status === "failed" && <span className="text-bad">Last collection failed{last.error ? `: ${last.error}` : ""}</span>}
        {refreshError && <span className="text-bad">{refreshError}</span>}
      </div>

      <div className="flex flex-wrap gap-1 border-b border-border mb-3">
        {catalog.map((c) => (
          <button
            key={c.key}
            onClick={() => setActive(c.key)}
            title={c.description}
            className={`px-3 py-1.5 text-sm -mb-px border-b-2 ${
              active === c.key ? "border-accent text-accent" : "border-transparent text-muted hover:text-text"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-3 mb-2">
        <input
          className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm w-72"
          placeholder={`Search ${active}…`}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="text-xs text-muted">
          {loading ? "Loading…" : data ? `${rows.length.toLocaleString()}${rows.length !== data.rows.length ? ` of ${data.rows.length.toLocaleString()}` : ""} rows` : ""}
        </span>
        <span className="text-xs text-muted ml-auto">{catalog.find((c) => c.key === active)?.description}</span>
      </div>

      {error && <div className="text-sm text-bad mb-2">{error}</div>}
      {data && (
        <Table
          key={active}
          rows={rows}
          columns={columns}
          storageKey={`reporting-${active}`}
          emptyMessage={data.rows.length === 0 ? "Nothing to report for this scope yet." : "No rows match your search."}
        />
      )}
    </div>
  );
}

function ExportItem({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="block w-full text-left px-3 py-1.5 text-text hover:bg-surface">
      {label}
    </button>
  );
}
