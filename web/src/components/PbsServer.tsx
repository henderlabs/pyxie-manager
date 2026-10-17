"use client";

import { useCallback, useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { PbsAccountsBuilder, PbsHostWrapperBuilder } from "@/components/HostSetupBuilder";
import PbsSshConnect from "@/components/PbsSshConnect";
import PbsTokenSlot, { type PbsToken } from "@/components/PbsTokenSlot";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Job = { id: string; store: string; schedule: string | null; settings: Record<string, string | number | boolean>; summary: string; last_state: string | null; last_end: number | null; next: number | null };
type Status = {
  reachable: boolean; error?: string; version?: string; release?: string; uptime?: number; cpu?: number; load?: number[]; kernel?: string; subscription?: string;
  memory?: { used: number; total: number } | null; root?: { used: number; total: number } | null;
  updates?: { count: number; packages: { package: string; current_version: string; new_version: string }[] } | null;
  tasks_running?: { type: string; id: string }[] | null; tasks_failed_24h?: { type: string; id: string; status: string; end: number }[] | null;
  datastores?: { name: string; used: number; total: number; estimated_full: number | null }[] | null; checked_at?: string;
};
type Server = {
  id: string; name: string; hostname: string; status: Status | null; status_at: string | null;
  credentials: Record<string, PbsToken & { token: string }>;
  ssh: { generated: boolean; status: string | null; host_key_pinned: boolean; fingerprint: string | null; wrapper_version: string | null; expected_wrapper_version: string; wrapper_outdated: boolean };
  jobs: Record<string, Job[]>; stores: string[]; remotes: string[]; error: string | null;
  datastores?: { name: string; backend: string; gc_schedule: string | null; gc: { last_state: string | null; last_end: number | null; next: number | null; removed_bytes: number | null; pending_bytes: number | null }; used: number | null; total: number | null }[];
};
type Check = { check: string; ok: boolean; detail: unknown };

const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const btn = "px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs";
const gb = (b?: number | null) => (b == null ? "—" : `${(b / GIB).toFixed(1)} GiB`);
const when = (s: number | null | undefined) => (s ? new Date(s * 1000).toLocaleString() : "—");
const days = (s?: number) => (s == null ? "—" : s > 86400 ? `${Math.floor(s / 86400)} days` : `${Math.floor(s / 3600)} hours`);
const SECTIONS: { kind: "sync" | "prune" | "verify"; label: string; help: string }[] = [
  { kind: "prune", label: "Prune jobs", help: "How long each datastore keeps backups." },
  { kind: "verify", label: "Verify jobs", help: "Re-reads backups to catch corruption." },
  { kind: "sync", label: "Sync jobs", help: "Copies backups between datastores or servers. This is how the offsite copy is fed." },
];
// field -> [label, type, help]
const FORM: Record<string, [string, string, string][]> = {
  prune: [["store", "store", "Datastore"], ["schedule", "text", "Schedule (e.g. 05:00, daily, monthly)"], ["keep-last", "number", "Keep last"], ["keep-hourly", "number", "Keep hourly"], ["keep-daily", "number", "Keep daily"], ["keep-weekly", "number", "Keep weekly"], ["keep-monthly", "number", "Keep monthly"], ["keep-yearly", "number", "Keep yearly"], ["comment", "text", "Comment"]],
  verify: [["store", "store", "Datastore"], ["schedule", "text", "Schedule (e.g. sun 06:00)"], ["outdated-after", "number", "Re-verify after (days)"], ["ignore-verified", "bool", "Skip backups already verified"], ["comment", "text", "Comment"]],
  sync: [["store", "store", "Pull into datastore"], ["remote", "remote", "From remote (empty = this server)"], ["remote-store", "text", "Remote datastore"], ["schedule", "text", "Schedule (e.g. monthly)"], ["remove-vanished", "bool", "Remove backups that vanished at the source"], ["transfer-last", "number", "Only the last N backups per group"], ["comment", "text", "Comment"]],
};

async function call(method: string, path: string, body?: unknown) {
  const r = await fetch(`/api/pbs/${path}`, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
  return d;
}

export default function PbsServer({ id }: { id: string }) {
  const me = useMe();
  const canEdit = me === undefined || me?.is_admin === true;
  const [s, setS] = useState<Server | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "datastores" | "jobs" | "patching" | "access">("overview");
  const load = useCallback(() => { call("GET", `servers/${id}`).then((d) => { setS(d); setErr(null); }).catch((e) => setErr(e.message)); }, [id]);
  useEffect(() => { load(); }, [load]);
  async function refresh() { try { await call("POST", `servers/${id}/refresh`); load(); } catch (e) { setErr((e as Error).message); } }
  if (err && !s) return <Card><div className="text-bad text-sm">{err}</div></Card>;
  if (!s) return <Card><div className="text-sm text-muted">Reading the backup server…</div></Card>;
  const st = s.status;
  const needsSetup = !s.credentials.inventory || !s.credentials.administrative || !s.ssh.host_key_pinned;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-lg text-text font-semibold">{s.name}</div>
          <div className="text-xs text-muted">{s.hostname} · {st?.reachable ? `Proxmox Backup Server ${st.version ?? ""}` : st ? <span className="text-bad">not reachable</span> : "not read yet"}
            {s.status_at && <> · read {new Date(s.status_at).toLocaleTimeString()}</>}</div>
        </div>
        <div className="flex items-center gap-3">
          <a href={`https://${s.hostname}:8007/`} target="_blank" rel="noreferrer" className="text-xs text-proxmox hover:underline">Open PBS</a>
          {canEdit && <button className={btn} onClick={refresh}>Refresh</button>}
        </div>
      </div>
      {err && <div className="text-bad text-sm">{err}</div>}
      {needsSetup && <div className="text-warn text-xs border border-warn/40 rounded p-2">Setup is not complete: {[!s.credentials.inventory && "read token", !s.credentials.administrative && "write token (needed to change jobs)", !s.ssh.host_key_pinned && "SSH access (needed to patch)"].filter(Boolean).join(", ")}. See Access, or <a href="/platform/credentials" className="underline">Settings, Credentials</a>.</div>}
      <div className="flex gap-1 border-b border-border text-sm">
        {(["overview", "datastores", "jobs", "patching", "access"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 -mb-px border-b-2 ${tab === t ? "border-proxmox text-text" : "border-transparent text-muted hover:text-text"}`}>
            {t === "overview" ? "Overview" : t === "datastores" ? "Datastores" : t === "jobs" ? "Jobs" : t === "patching" ? `Patching${st?.updates?.count ? ` (${st.updates.count})` : ""}` : "Access"}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview s={s} />}
      {tab === "datastores" && <Datastores s={s} canEdit={canEdit} reload={load} />}
      {tab === "jobs" && <Jobs s={s} canEdit={canEdit} reload={load} />}
      {tab === "patching" && <Card><CardTitle>Patching</CardTitle><div className="text-sm">Package updates for backup servers are managed on the <a href="/operations/maintenance#backup-servers" className="text-accent hover:underline">Maintenance page</a>, next to the Proxmox hosts. {s.status?.updates?.count ? <span className="text-warn">{s.status.updates.count} package(s) are waiting on this server.</span> : null}</div></Card>}
      {tab === "access" && <Access s={s} canEdit={canEdit} reload={load} />}
    </div>
  );
}

function Overview({ s }: { s: Server }) {
  const st = s.status;
  if (!st) return <Card><div className="text-sm text-muted">No status yet. PyXie reads it every few minutes once a read token is saved (Access).</div></Card>;
  if (!st.reachable) return <Card><div className="text-sm text-bad">PyXie cannot reach this server: {st.error ?? "no answer"}</div></Card>;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card>
        <CardTitle>Server</CardTitle>
        <div className="text-sm space-y-1">
          <div><span className="text-muted">Version </span>{st.version}-{st.release}</div>
          <div><span className="text-muted">Kernel </span>{typeof st.kernel === "string" ? st.kernel : "—"}</div>
          <div><span className="text-muted">Up </span>{days(st.uptime)}</div>
          <div><span className="text-muted">Load </span>{st.load ? st.load.map((x) => x.toFixed(2)).join(" ") : "—"} <span className="text-muted ml-2">CPU </span>{st.cpu != null ? `${(st.cpu * 100).toFixed(0)}%` : "—"}</div>
          <div><span className="text-muted">Memory </span>{gb(st.memory?.used)} of {gb(st.memory?.total)}</div>
          <div><span className="text-muted">System disk </span>{gb(st.root?.used)} of {gb(st.root?.total)}</div>
          <div><span className="text-muted">Subscription </span>{st.subscription ?? "unknown"}</div>
          <div><span className="text-muted">Patches </span>{st.updates == null ? "not readable with this token" : st.updates.count === 0 ? <span className="text-good">up to date</span> : <span className="text-warn">{st.updates.count} waiting (see Patching)</span>}</div>
        </div>
      </Card>
      <Card>
        <CardTitle>Datastores</CardTitle>
        {st.datastores == null ? <div className="text-sm text-muted">Not readable with this token.</div> : st.datastores.map((d) => {
          const pct = d.total ? (100 * d.used) / d.total : 0;
          return (
            <div key={d.name} className="mb-3 text-sm">
              <div className="flex justify-between"><span className="text-text">{d.name}</span><span className="text-muted text-xs">{gb(d.used)} of {gb(d.total)} ({pct.toFixed(0)}%)</span></div>
              <div className="h-2 bg-surface2 rounded overflow-hidden"><div className={`h-full ${pct >= 95 ? "bg-bad" : pct >= 85 ? "bg-warn" : "bg-good"}`} style={{ width: `${Math.min(100, pct)}%` }} /></div>
              {d.estimated_full && <div className="text-xs text-muted mt-0.5">Estimated full {when(d.estimated_full)}</div>}
            </div>
          );
        })}
      </Card>
      <Card>
        <CardTitle>Running now</CardTitle>
        {st.tasks_running == null ? <div className="text-sm text-muted">Not readable with this token.</div> : st.tasks_running.length === 0 ? <div className="text-sm text-muted">Nothing is running.</div> :
          st.tasks_running.map((t, i) => <div key={i} className="text-sm">{t.type} <span className="text-muted">{t.id}</span></div>)}
      </Card>
      <Card>
        <CardTitle>Failed in the last 24 hours</CardTitle>
        {st.tasks_failed_24h == null ? <div className="text-sm text-muted">Not readable with this token.</div> : st.tasks_failed_24h.length === 0 ? <div className="text-sm text-good">No failures.</div> :
          st.tasks_failed_24h.map((t, i) => <div key={i} className="text-sm text-bad">{t.type} <span className="text-muted">{t.id} · {when(t.end)} · {t.status}</span></div>)}
      </Card>
    </div>
  );
}

function Datastores({ s, canEdit, reload }: { s: Server; canEdit: boolean; reload: () => void }) {
  const [pend, setPend] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [sched, setSched] = useState("");
  const hasAdmin = !!s.credentials.administrative;
  async function run(store: string, action: string, params: Record<string, unknown>) {
    setBusy(true); setErr(null);
    try { const d = await call("POST", `servers/${s.id}/datastores/dry-run`, { store, action, params }); setPend(d); setEditing(null); notifyOperationsChanged(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (pend) return <OpPreview key={pend.id} op={pend} confirmLabel="Confirm" progressText="Working on the backup server" doneText="Done and read back from the backup server (a garbage collection or verify run continues there and can take hours)." onFinished={() => { reload(); notifyOperationsChanged(); }} onClose={() => { setPend(null); reload(); }} />;
  return (
    <div className="space-y-4">
      {!hasAdmin && canEdit && <div className="text-warn text-xs">Datastore settings are read-only until a write token is saved (Access, step 2).</div>}
      {(s.datastores ?? []).map((d) => (
        <Card key={d.name}>
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div><div className="text-text font-medium">{d.name} <span className="text-xs text-muted font-normal">{d.backend === "s3" ? "S3 object storage" : "disk"}</span></div>
              <div className="text-xs text-muted">{d.used != null && d.total ? `${gb(d.used)} of ${gb(d.total)} used` : ""}</div></div>
            {canEdit && hasAdmin && <div className="flex gap-2">
              <button className={btn} disabled={busy} onClick={() => run(d.name, "gc_run", {})}>Run garbage collection now</button>
              <button className={btn} disabled={busy} onClick={() => run(d.name, "verify_run", {})}>Verify everything now</button>
            </div>}
          </div>
          <div className="text-sm mt-3 grid grid-cols-1 md:grid-cols-3 gap-3">
            <div><div className="text-xs text-muted">Garbage collection schedule</div>
              {editing === d.name ? (
                <div className="flex gap-2 mt-1"><input className={input} value={sched} onChange={(e) => setSched(e.target.value)} placeholder="e.g. sat 18:15, daily; empty = never" />
                  <button className={btn} disabled={busy} onClick={() => run(d.name, "gc_schedule", { schedule: sched })}>Preview</button><button className="text-xs text-muted" onClick={() => setEditing(null)}>cancel</button></div>
              ) : <div>{d.gc_schedule ?? <span className="text-warn">none: it only runs when started by hand</span>} {canEdit && hasAdmin && <button className="text-xs text-accent hover:underline ml-2" onClick={() => { setEditing(d.name); setSched(d.gc_schedule ?? ""); }}>Edit</button>}</div>}</div>
            <div><div className="text-xs text-muted">Last garbage collection</div>
              <div className={d.gc.last_state && d.gc.last_state !== "OK" ? "text-bad" : ""}>{d.gc.last_state ? `${d.gc.last_state}, ${when(d.gc.last_end)}` : "never"}{d.gc.removed_bytes != null ? <span className="text-xs text-muted"> · freed {gb(d.gc.removed_bytes)}</span> : null}</div></div>
            <div><div className="text-xs text-muted">Next scheduled run</div><div>{when(d.gc.next)}</div></div>
          </div>
        </Card>
      ))}
      {(s.datastores ?? []).length === 0 && <Card><div className="text-sm text-muted">No datastores are readable with this token.</div></Card>}
      {err && <div className="text-bad text-xs">{err}</div>}
      <p className="text-xs text-muted">Garbage collection frees chunks no backup uses; it deletes no backup. Verifying re-reads backups and checks them against their checksums (verify one backup from a guest's restore points on Protection). Prune and verify schedules are on the Jobs tab.</p>
    </div>
  );
}

function Jobs({ s, canEdit, reload }: { s: Server; canEdit: boolean; reload: () => void }) {
  const [form, setForm] = useState<{ kind: "sync" | "prune" | "verify"; action: "create" | "update"; job?: Job } | null>(null);
  const [vals, setVals] = useState<Record<string, string | boolean>>({});
  const [jobId, setJobId] = useState("");
  const [pend, setPend] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const hasAdmin = !!s.credentials.administrative;

  function open(kind: "sync" | "prune" | "verify", action: "create" | "update", job?: Job) {
    setForm({ kind, action, job }); setJobId(job?.id ?? ""); setErr(null);
    const v: Record<string, string | boolean> = {};
    for (const [k, type] of FORM[kind].map((f) => [f[0], f[1]])) {
      const cur = job?.settings?.[k];
      v[k] = type === "bool" ? Boolean(cur) : cur == null ? "" : String(cur);
    }
    setVals(v);
  }
  async function dryRun(kind: string, action: string, id: string, params: Record<string, unknown>) {
    setBusy(true); setErr(null);
    try { const d = await call("POST", `servers/${s.id}/jobs/dry-run`, { kind, action, job_id: id, params }); setPend(d); setForm(null); notifyOperationsChanged(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  function submit() {
    if (!form) return;
    const params: Record<string, unknown> = {};
    for (const [k, type] of FORM[form.kind].map((f) => [f[0], f[1]])) {
      const cur = form.job?.settings?.[k];
      const v = vals[k];
      if (type === "bool") { if (form.action === "create" ? v : Boolean(cur) !== v) params[k] = v; }
      else if (form.action === "create") { if (v !== "") params[k] = v; }
      else if (String(cur ?? "") !== v) params[k] = v;
    }
    dryRun(form.kind, form.action, jobId, params);
  }
  if (pend) return <OpPreview key={pend.id} op={pend} confirmLabel="Confirm" progressText="Changing the PBS job" doneText="Done and read back from PBS." onFinished={() => { reload(); notifyOperationsChanged(); }} onClose={() => { setPend(null); reload(); }} />;
  return (
    <div className="space-y-4">
      {s.error && <div className="text-warn text-sm">{s.error}</div>}
      {!hasAdmin && canEdit && <div className="text-warn text-xs">Jobs are read-only until a write token is saved (Access, step 2).</div>}
      {SECTIONS.map((sec) => (
        <Card key={sec.kind}>
          <div className="flex items-center justify-between mb-2">
            <div><CardTitle>{sec.label}</CardTitle><div className="text-xs text-muted -mt-1">{sec.help}</div></div>
            {canEdit && hasAdmin && <button className={btn} onClick={() => open(sec.kind, "create")}>New job</button>}
          </div>
          {(s.jobs[sec.kind] ?? []).length === 0 ? <div className="text-sm text-muted">No {sec.kind} jobs are visible.{sec.kind === "sync" && " If one exists in PBS, the read token may not be allowed to list it (Access)."}</div> : (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-muted"><th className="py-1 pr-3">Job</th><th className="pr-3">Datastore</th><th className="pr-3">Schedule</th><th className="pr-3">Does</th><th className="pr-3">Last run</th><th className="pr-3">Next run</th><th></th></tr></thead>
              <tbody className="divide-y divide-border">
                {s.jobs[sec.kind].map((j) => (
                  <tr key={j.id}>
                    <td className="py-1.5 pr-3 text-text">{j.id}</td><td className="pr-3">{j.store}</td><td className="pr-3">{j.schedule ?? "—"}</td><td className="pr-3 text-xs text-muted">{j.summary}</td>
                    <td className={`pr-3 text-xs ${j.last_state && j.last_state !== "OK" ? "text-bad" : ""}`}>{j.last_state ? `${j.last_state}, ${when(j.last_end)}` : "never"}</td><td className="pr-3 text-xs">{when(j.next)}</td>
                    <td className="text-xs whitespace-nowrap">{canEdit && hasAdmin && <>
                      <button className="text-accent hover:underline mr-2" onClick={() => open(sec.kind, "update", j)}>Edit</button>
                      <button className="text-accent hover:underline mr-2" disabled={busy} onClick={() => dryRun(sec.kind, "run", j.id, {})}>Run now</button>
                      <button className="text-bad hover:underline" disabled={busy} onClick={() => dryRun(sec.kind, "delete", j.id, {})}>Delete</button></>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      ))}
      {form && (
        <Card>
          <CardTitle>{form.action === "create" ? `New ${form.kind} job` : `Edit ${form.kind} job ${form.job?.id}`}</CardTitle>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            {form.action === "create" && <label><div className="text-xs text-muted mb-1">Job name</div><input className={input} value={jobId} onChange={(e) => setJobId(e.target.value)} placeholder="e.g. monthly-offsite" /></label>}
            {FORM[form.kind].map(([k, type, label]) => (
              <label key={k} className={type === "bool" ? "flex items-center gap-2 mt-5" : ""}>
                {type === "bool" ? <><input type="checkbox" checked={Boolean(vals[k])} onChange={(e) => setVals({ ...vals, [k]: e.target.checked })} /><span>{label}</span></> : <>
                  <div className="text-xs text-muted mb-1">{label}</div>
                  {type === "store" ? <select className={input} value={String(vals[k] ?? "")} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}><option value="">choose…</option>{s.stores.map((x) => <option key={x}>{x}</option>)}</select>
                    : type === "remote" ? <select className={input} value={String(vals[k] ?? "")} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}><option value="">this server</option>{s.remotes.map((x) => <option key={x}>{x}</option>)}</select>
                    : <input className={input} type={type === "number" ? "number" : "text"} min={1} value={String(vals[k] ?? "")} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />}</>}
              </label>
            ))}
          </div>
          {err && <div className="text-bad text-xs mt-2">{err}</div>}
          <div className="flex gap-2 mt-3"><button className={btn} disabled={busy} onClick={submit}>{busy ? "Checking…" : "Preview"}</button><button className="text-xs text-muted hover:text-text" onClick={() => setForm(null)}>cancel</button></div>
          <p className="text-xs text-muted mt-2">Nothing changes in PBS until you confirm the preview. Clearing a field removes that setting.</p>
        </Card>
      )}
      {err && !form && <div className="text-bad text-xs">{err}</div>}
    </div>
  );
}

function Access({ s, canEdit, reload }: { s: Server; canEdit: boolean; reload: () => void }) {
  const tokenCard = (slot: "inventory" | "administrative" | "restore" | "backup", title: string, why: string) => (
    <Card>
      <CardTitle>{title}</CardTitle>
      <p className="text-xs text-muted mb-2">{why}</p>
      <PbsTokenSlot serverId={s.id} slot={slot} token={s.credentials[slot] as PbsToken | undefined} onChanged={reload} />
    </Card>
  );
  return (
    <div className="space-y-4">
      <Card><div className="text-sm text-muted">A backup server is set up like a Proxmox host: a read token so PyXie can see it (step 1), a write token so PyXie can edit its sync, prune and verify jobs (step 2), and SSH access so PyXie can patch it (step 3). Only step 1 is needed to watch it. All saved tokens are also listed under Platform, Credentials. PyXie never creates users or keys on the PBS itself; you run the commands below.</div></Card>
      <Card><CardTitle>Step 0: create the accounts on the backup server</CardTitle><PbsAccountsBuilder /></Card>
      {tokenCard("inventory", "Step 1: read token", "Where the read-only token is saved. Both the user and its token carry the grants (a token can never do more than its user); RemoteAudit on /remote is what lets PyXie see sync jobs, which the Audit role does not include.")}
      {tokenCard("restore", "Step 4: restore token (optional, to restore from datastores Proxmox does not have yet)", "Read-only access to backup data (DatastoreReader). Used only to add a datastore, such as the offsite copy, to Proxmox as a backup storage so you can restore from it. It cannot write or delete backups.")}
      {tokenCard("backup", "Step 5: backup token (optional, to back guests up into a namespace)", "DatastorePowerUser: Proxmox writes backups and prunes the ones it owns. Used only when you add a datastore or namespace to Proxmox as a backup target (Storage, Backup namespaces).")}
      {tokenCard("administrative", "Step 2: write token (to edit jobs)", "Used only for creating, changing, deleting and running sync, prune and verify jobs, and only after you confirm a preview. It cannot install packages or touch the operating system.")}
      <Card>
        <CardTitle>Step 3: SSH access (to patch)</CardTitle>
        <p className="text-xs text-muted mb-2">Same restricted wrapper and pinned host key PyXie uses on Proxmox hosts: one allowed command, no shell. Neither the PVE nor the PBS API can install updates, only list them, so patching needs this.</p>
        <div className="mb-3"><PbsHostWrapperBuilder servers={[{ id: s.id, name: s.name }]} tlsMode="" /></div>
        <PbsSshConnect serverId={s.id} serverName={s.name} ssh={s.ssh} pending={s.status?.updates?.count ?? null} onChanged={reload} />
      </Card>
    </div>
  );
}
