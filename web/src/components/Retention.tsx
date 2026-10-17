"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { BackupJob, Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type PbsJob = { id: string; ns?: string; store: string; schedule: string | null; settings: Record<string, string | number | boolean>; summary: string; last_state: string | null; last_end: number | null; next: number | null };
type Ds = { name: string; backend: string; gc_schedule: string | null; gc: { last_state: string | null; last_end: number | null; next: number | null; removed_bytes: number | null }; used: number | null; total: number | null };
type Srv = { id: string; name: string; credentials: Record<string, unknown>; jobs: Record<string, PbsJob[]>; datastores?: Ds[]; error: string | null };
type PlanLite = { id: string; name: string; is_default: boolean; datastore: string | null; job_id: string; layers: { store: string; namespace: string }[] };

const KEEP = ["keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly"] as const;
const input = "bg-surface border border-border rounded px-2 py-1 text-sm w-full";
const btn = "px-2.5 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs";
const when = (s: number | null | undefined) => (s ? new Date(s * 1000).toLocaleString() : "never");
const GIB = 1073741824;

type Tone = "plain" | "good" | "warn" | "bad" | "accent";
const tones: Record<Tone, string> = { plain: "bg-surface2 text-text", good: "bg-good/15 text-good", warn: "bg-warn/15 text-warn", bad: "bg-bad/15 text-bad", accent: "bg-accent/15 text-accent" };
function Pill({ children, tone = "plain", title }: { children: React.ReactNode; tone?: Tone; title?: string }) {
  return <span title={title} className={`inline-block px-2 py-0.5 rounded-full text-xs whitespace-nowrap ${tones[tone]}`}>{children}</span>;
}
function Row({ label, help, children, actions }: { label: string; help: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-[11rem_1fr_auto] gap-x-4 gap-y-1 py-2 border-t border-border items-start">
      <div><div className="text-sm text-text">{label}</div><div className="text-xs text-muted">{help}</div></div>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
      <div className="flex flex-wrap items-center gap-3 text-xs">{actions}</div>
    </div>
  );
}
const keepPills = (k: Record<string, unknown>) => {
  const parts = KEEP.filter((x) => k[x]).map((x) => `${k[x]} ${x.slice(5)}`);
  return parts.length ? parts.map((p) => <Pill key={p} tone="accent">keep {p}</Pill>) : [<Pill key="all" tone="warn">keeps everything</Pill>];
};

async function post(path: string, body: unknown) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
  return d as Operation;
}

function KeepEditor({ initial, schedule, withSchedule, busy, onPreview, onCancel }: { initial: Record<string, unknown>; schedule?: string | null; withSchedule: boolean; busy: boolean; onPreview: (keep: Record<string, string>, schedule: string) => void; onCancel: () => void }) {
  const [v, setV] = useState<Record<string, string>>(Object.fromEntries(KEEP.map((k) => [k, initial[k] ? String(initial[k]) : ""])));
  const [sched, setSched] = useState(schedule ?? "");
  return (
    <div className="mt-2 p-3 rounded border border-border bg-surface2/40 w-full">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2">{KEEP.map((k) => <label key={k}><div className="text-[11px] text-muted mb-0.5">Keep {k.slice(5)}</div><input className={input} type="number" min={1} value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></label>)}</div>
      {withSchedule && <label className="block mt-2 max-w-xs"><div className="text-[11px] text-muted mb-0.5">Runs (for example 05:00, daily, sat 18:15)</div><input className={input} value={sched} onChange={(e) => setSched(e.target.value)} /></label>}
      <div className="flex items-center gap-3 mt-2"><button className={btn} disabled={busy} onClick={() => onPreview(v, sched)}>{busy ? "Checking…" : "Preview"}</button><button className="text-xs text-muted hover:text-text" onClick={onCancel}>cancel</button>
        <span className="text-xs text-muted">Nothing changes until you confirm the preview.</span></div>
    </div>
  );
}

/** Backup servers page: one card per datastore with its prune, free-space, verify and copy settings as pills, and a card for the Proxmox backup jobs. Plan names link to the plan. */
export default function Retention({ servers, jobs }: { servers: { id: string; name: string }[]; jobs: BackupJob[] }) {
  const me = useMe();
  const canEdit = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<Srv[] | null>(null);
  const [plans, setPlans] = useState<PlanLite[]>([]);
  const [edit, setEdit] = useState<string | null>(null);
  const [gcEdit, setGcEdit] = useState<string | null>(null);
  const [gcVal, setGcVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [pend, setPend] = useState<{ op: Operation; text: string } | null>(null);
  const load = useCallback(async () => {
    const out: Srv[] = [];
    for (const s of servers) { try { const r = await fetch(`/api/pbs/servers/${s.id}`); if (r.ok) out.push(await r.json()); } catch { /* shown as missing */ } }
    setData(out);
    try { const c = await (await fetch("/api/defaults")).json(); if (c.clusters?.[0]) { const o = await (await fetch(`/api/backup-policies?cluster_id=${c.clusters[0].id}`)).json(); setPlans(o.policies ?? []); } } catch { /* plans are optional here */ }
  }, [servers]);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<Operation>, text: string) {
    setBusy(true); setErr(null);
    try { const op = await fn(); setPend({ op, text }); setEdit(null); setGcEdit(null); notifyOperationsChanged(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const diffKeep = (job: PbsJob | undefined, keep: Record<string, string>, schedule: string) => {
    const p: Record<string, unknown> = {};
    for (const k of KEEP) if (String(job?.settings?.[k] ?? "") !== keep[k]) p[k] = keep[k];
    if (job ? String(job.schedule ?? "") !== schedule : schedule !== "") p.schedule = schedule;
    return p;
  };
  const planLink = (p: PlanLite, what?: string) => <Link key={p.id + (what ?? "")} href={`/operations/protection#plan-${p.id}`} className="inline-block px-2 py-0.5 rounded-full text-xs bg-proxmox/15 text-proxmox hover:underline" title="Open this plan's settings">{p.name}{what ? ` · ${what}` : ""}</Link>;

  if (pend) return <OpPreview key={pend.op.id} op={pend.op} confirmLabel={pend.text} progressText="Changing the setting" doneText="Done and read back." onFinished={() => { load(); notifyOperationsChanged(); }} onClose={() => { setPend(null); window.location.reload(); }} />;
  return (
    <div className="space-y-4 text-sm">
      {data === null && <div className="text-muted">Reading the backup servers…</div>}
      {(data ?? []).map((s) => {
        const hasAdmin = !!s.credentials.administrative;
        const can = canEdit && hasAdmin;
        return (
          <div key={s.id} className="space-y-3">
            {s.error && <div className="text-warn text-xs">{s.name}: {s.error}</div>}
            {!hasAdmin && canEdit && <div className="text-warn text-xs">{s.name} is read-only here until its write token is saved (Settings, Credentials).</div>}
            {(s.datastores ?? []).map((d) => {
              const prune = (s.jobs.prune ?? []).filter((j) => j.store === d.name);
              const verify = (s.jobs.verify ?? []).filter((j) => j.store === d.name);
              const sync = (s.jobs.sync ?? []).filter((j) => j.store === d.name);
              const used = plans.filter((p) => p.datastore === d.name || p.layers.some((l) => l.store === d.name));
              const pct = d.total && d.used != null ? Math.round((100 * d.used) / d.total) : null;
              return (
                <div key={d.name} className="rounded border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <span className="text-text font-medium text-base">{d.name}</span>
                    <Pill tone={d.backend === "s3" ? "accent" : "plain"}>{d.backend === "s3" ? "S3 · offsite" : "disk"}</Pill>
                    <Pill>{s.name}</Pill>
                    {pct != null && <Pill tone={pct >= 85 ? "warn" : "plain"} title={`${((d.used ?? 0) / GIB).toFixed(0)} GiB of ${((d.total ?? 0) / GIB).toFixed(0)} GiB`}>{pct}% full</Pill>}
                  </div>
                  <Row label="Used by plans" help="Which plans write to this datastore.">
                    {used.length === 0 ? <Pill tone="warn">no plan uses it</Pill> : used.map((p) => planLink(p, p.datastore === d.name ? "first backup" : "copy"))}
                  </Row>
                  <Row label="Pruning" help="Deletes backups outside the keep rules."
                    actions={can && prune.filter((j) => !j.ns).length === 0 && (edit === `${s.id}:new:${d.name}` ? null : <button className="text-accent hover:underline" onClick={() => setEdit(`${s.id}:new:${d.name}`)}>Add a prune job</button>)}>
                    {prune.length === 0 && <Pill tone="warn">no prune job: nothing is deleted here</Pill>}
                    {prune.map((j) => (
                      <span key={j.id} className="inline-flex flex-wrap items-center gap-1.5 mr-3">
                        <span className="text-xs text-muted">{j.id}</span>{keepPills(j.settings)}<Pill>runs {j.schedule ?? "never"}</Pill>
                        {j.ns ? <Pill tone="accent">namespace {j.ns}</Pill> : null}
                        <Pill tone={j.last_state && j.last_state !== "OK" ? "bad" : "plain"}>last {j.last_state ? `${j.last_state}, ${when(j.last_end)}` : "never run"}</Pill>
                        {can && edit !== `${s.id}:${j.id}` && <button className="text-xs text-accent hover:underline" onClick={() => setEdit(`${s.id}:${j.id}`)}>Edit</button>}
                        {edit === `${s.id}:${j.id}` && <KeepEditor initial={j.settings} schedule={j.schedule} withSchedule busy={busy} onCancel={() => setEdit(null)}
                          onPreview={(k, sc) => run(() => post(`/api/pbs/servers/${s.id}/jobs/dry-run`, { kind: "prune", action: "update", job_id: j.id, params: diffKeep(j, k, sc) }), "Change pruning")} />}
                      </span>
                    ))}
                    {edit === `${s.id}:new:${d.name}` && <KeepEditor initial={{}} schedule="daily" withSchedule busy={busy} onCancel={() => setEdit(null)}
                      onPreview={(k, sc) => run(() => post(`/api/pbs/servers/${s.id}/jobs/dry-run`, { kind: "prune", action: "create", job_id: `prune-${d.name}`.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 30), params: { store: d.name, ...Object.fromEntries(Object.entries(diffKeep(undefined, k, sc)).filter(([, x]) => x !== "")) } }), "Create prune job")} />}
                  </Row>
                  <Row label="Freeing space" help="Garbage collection removes data no backup uses. One schedule per datastore."
                    actions={can && gcEdit !== `${s.id}:${d.name}` && <><button className="text-accent hover:underline" onClick={() => { setGcEdit(`${s.id}:${d.name}`); setGcVal(d.gc_schedule ?? ""); }}>Edit</button>
                      <button className="text-accent hover:underline" disabled={busy} onClick={() => run(() => post(`/api/pbs/servers/${s.id}/datastores/dry-run`, { store: d.name, action: "gc_run", params: {} }), "Run now")}>Run now</button></>}>
                    {gcEdit === `${s.id}:${d.name}` ? (
                      <span className="flex items-center gap-2"><input className={input + " !w-56"} value={gcVal} onChange={(e) => setGcVal(e.target.value)} placeholder="for example sat 18:15; empty = never" />
                        <button className={btn} disabled={busy} onClick={() => run(() => post(`/api/pbs/servers/${s.id}/datastores/dry-run`, { store: d.name, action: "gc_schedule", params: { schedule: gcVal } }), "Change schedule")}>Preview</button>
                        <button className="text-xs text-muted" onClick={() => setGcEdit(null)}>cancel</button></span>
                    ) : <>
                      {d.gc_schedule ? <Pill>runs {d.gc_schedule}</Pill> : <Pill tone="warn" title="Pruned data keeps using space until someone runs it by hand">no schedule{d.backend === "s3" ? ": S3 keeps billing for unused data" : ""}</Pill>}
                      <Pill tone={d.gc.last_state && d.gc.last_state !== "OK" ? "bad" : "plain"}>last {d.gc.last_state ? `${d.gc.last_state}, ${when(d.gc.last_end)}` : "never run"}</Pill>
                      {d.gc.removed_bytes != null && <Pill>freed {(d.gc.removed_bytes / GIB).toFixed(1)} GiB</Pill>}</>}
                  </Row>
                  <Row label="Verifying" help="Re-reads backups and checks them against their checksums.">
                    {verify.length === 0 ? <Pill tone="warn">no verify job: backups are never checked for corruption</Pill> : verify.map((j) => (
                      <span key={j.id} className="inline-flex flex-wrap items-center gap-1.5 mr-3"><span className="text-xs text-muted">{j.id}</span><Pill>runs {j.schedule ?? "never"}</Pill>
                        {j.settings["outdated-after"] ? <Pill>checks again after {String(j.settings["outdated-after"])} days</Pill> : null}
                        <Pill tone={j.last_state && j.last_state !== "OK" ? "bad" : "plain"}>last {j.last_state ? `${j.last_state}, ${when(j.last_end)}` : "never run"}</Pill></span>))}
                  </Row>
                  <Row label="Copies arriving here" help="Sync jobs that copy backups into this datastore.">
                    {sync.length === 0 ? <span className="text-xs text-muted">none</span> : sync.map((j) => (
                      <span key={j.id} className="inline-flex flex-wrap items-center gap-1.5 mr-3"><span className="text-xs text-muted">{j.id}</span><Pill>from {String(j.settings["remote-store"] ?? "?")}</Pill><Pill>runs {j.schedule ?? "never"}</Pill>
                        {j.ns ? <Pill tone="accent">into namespace {j.ns}</Pill> : null}
                        <Pill tone={j.last_state && j.last_state !== "OK" ? "bad" : "plain"}>last {j.last_state ? `${j.last_state}, ${when(j.last_end)}` : "never run"}</Pill></span>))}
                  </Row>
                </div>
              );
            })}
          </div>
        );
      })}

      <div className="rounded border border-border p-3">
        <div className="text-text font-medium text-base">Proxmox backup jobs</div>
        <div className="text-xs text-muted mb-1">The jobs Proxmox runs. A plan creates and keeps these; the retention here is what each job prunes after it runs.</div>
        {jobs.length === 0 && <div className="text-muted mt-1">No backup jobs write to a backup server.</div>}
        {jobs.map((j) => {
          const k = `${j.cluster_id}:${j.job_id}`;
          const plan = plans.find((p) => p.job_id === j.job_id);
          return (
            <div key={k} className="py-2 border-t border-border">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-text mr-1">{j.job_id}</span>
                {plan ? planLink(plan) : <Pill tone="warn" title="No plan manages this job">not managed by a plan</Pill>}
                <Pill>{j.schedule}</Pill><Pill>to {j.storage}</Pill>
                {j.prune_backups && Object.keys(j.prune_backups).length ? keepPills(Object.fromEntries(Object.entries(j.prune_backups).map(([a, b]) => [a.replace("_", "-"), b]))) : <Pill title="The datastore's prune jobs decide">no limit on the job</Pill>}
                {!j.enabled && <Pill tone="warn">switched off</Pill>}
                {canEdit && edit !== k && <button className="text-xs text-accent hover:underline ml-1" onClick={() => setEdit(k)}>Edit retention</button>}
              </div>
              {edit === k && <KeepEditor initial={Object.fromEntries(Object.entries(j.prune_backups ?? {}).map(([a, b]) => [a.replace("_", "-"), b]))} withSchedule={false} busy={busy} onCancel={() => setEdit(null)}
                onPreview={(keep) => run(() => post("/api/protection/backup-jobs/retention/dry-run", { cluster_id: j.cluster_id, job_id: j.job_id, keep }), "Change retention")} />}
            </div>
          );
        })}
      </div>
      {err && <div className="text-bad text-xs">{err}</div>}
    </div>
  );
}
