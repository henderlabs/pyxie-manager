"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Layer = {
  id?: string; label: string; target_id: string; store: string; namespace: string; schedule: string; what: "all" | "last"; last: number | null; verified_only: boolean; remove_vanished: boolean;
  prune_schedule: string; gc_schedule: string | null; enabled: boolean; text?: string; server?: string | null;
} & Record<string, unknown>;
type Maint = { prune: string | null; gc: string | null; verify: string | null; verify_after: number };
type Plan = {
  id: string; name: string; storage: string; schedule: string; mode: string; enabled: boolean; notes_template: string | null; job_id: string; retention: string;
  keep_last: number | null; keep_hourly: number | null; keep_daily: number | null; keep_weekly: number | null; keep_monthly: number | null; keep_yearly: number | null;
  job: { id: string } | null; datastore: string | null; warnings: { level: string; text: string }[]; is_default: boolean; tags: string[]; guest_count: number; set_by_hand: number;
  layers: Layer[]; maintenance: Maint; three_two_one: { ok: boolean; copies: number; missing: string[] };
};
type Store = { name: string; backend: string; gc_schedule: string | null; namespaces: string[]; prune: { id: string; ns: string; summary: string }[] };
type Server = { id: string; name: string; has_remote: boolean; stores: Store[] };
type Overview = { servers: Server[]; policies: Plan[]; tags: string[]; storages: { name: string; type: string | null; datastore?: string | null; namespace?: string }[]; drift: { action: string; job_id: string; why: string }[] };

const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const KEEPS: [string, string, string][] = [
  ["keep_daily", "Daily", "Keep the newest backup of each of the last N days that have one."], ["keep_weekly", "Weekly", "Keep the newest backup of each of the last N weeks."],
  ["keep_monthly", "Monthly", "Keep the newest backup of each of the last N months."], ["keep_yearly", "Yearly", "Keep the newest backup of each of the last N years."],
  ["keep_last", "Newest", "Always keep the newest N backups, whatever their age."], ["keep_hourly", "Hourly", "Keep the newest backup of each of the last N hours."],
];
const MODES = [{ v: "snapshot", l: "Snapshot (recommended)" }, { v: "suspend", l: "Suspend" }, { v: "stop", l: "Stop (guest is down during the backup)" }];

function scheduleOf(freq: string, time: string, day: string, dom: string, custom: string): string {
  if (freq === "daily") return time;
  if (freq === "weekly") return `${day} ${time}`;
  if (freq === "monthly") return `*-*-${dom.padStart(2, "0")} ${time}`;
  return custom.trim();
}
function readSchedule(s: string) {
  const d = { freq: "custom", time: "02:00", day: "sat", dom: "1", custom: s };
  let m = s.match(/^(\d{1,2}:\d{2})$/); if (m) return { ...d, freq: "daily", time: m[1].padStart(5, "0") };
  m = s.match(/^(mon|tue|wed|thu|fri|sat|sun) (\d{1,2}:\d{2})$/); if (m) return { ...d, freq: "weekly", day: m[1], time: m[2].padStart(5, "0") };
  m = s.match(/^\*-\*-(\d{1,2}) (\d{1,2}:\d{2})$/); if (m) return { ...d, freq: "monthly", dom: String(parseInt(m[1], 10)), time: m[2].padStart(5, "0") };
  return d;
}
/** A schedule in words: "02:00" is every night at 02:00. */
function human(s: string): string {
  const r = readSchedule(s);
  if (r.freq === "daily") return `every night at ${r.time}`;
  if (r.freq === "weekly") return `every ${r.day} at ${r.time}`;
  if (r.freq === "monthly") return `monthly (day ${r.dom}) at ${r.time}`;
  return s;
}
const keepWords = (p: Record<string, unknown>) => {
  const n: Record<string, string> = { keep_last: "newest", keep_hourly: "hourly", keep_daily: "daily", keep_weekly: "weekly", keep_monthly: "monthly", keep_yearly: "yearly" };
  const parts = Object.keys(n).filter((k) => p[k]).map((k) => `${p[k]} ${n[k]}`);
  return parts.length ? `keep ${parts.join(", ")}` : "keep everything";
};

/** A when picker: every night / week / month at a time, or a custom calendar event. */
function When({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const init = readSchedule(value);
  const [freq, setFreq] = useState(init.freq);
  const [time, setTime] = useState(init.time);
  const [day, setDay] = useState(init.day);
  const [dom, setDom] = useState(init.dom);
  const [custom, setCustom] = useState(init.custom);
  useEffect(() => { onChange(scheduleOf(freq, time, day, dom, custom)); }, [freq, time, day, dom, custom]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <select className={input + " !w-auto"} value={freq} onChange={(e) => setFreq(e.target.value)}><option value="daily">every night</option><option value="weekly">every week</option><option value="monthly">every month</option><option value="custom">custom…</option></select>
      {freq === "weekly" && <select className={input + " !w-auto"} value={day} onChange={(e) => setDay(e.target.value)}>{DAYS.map((d) => <option key={d}>{d}</option>)}</select>}
      {freq === "monthly" && <><span className="text-muted text-xs">on day</span><input type="number" min={1} max={31} className={input + " !w-16"} value={dom} onChange={(e) => setDom(e.target.value)} /></>}
      {freq !== "custom" ? <><span className="text-muted text-xs">at</span><input type="time" className={input + " !w-auto"} value={time} onChange={(e) => setTime(e.target.value)} /></> : <input className={input + " !w-44"} value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="sat 18:15" />}
    </span>
  );
}

/** "Keep" boxes: the four that matter, the rest behind a link. */
function Keep({ keep, setKeep }: { keep: Record<string, string>; setKeep: (k: Record<string, string>) => void }) {
  const [more, setMore] = useState(!!(keep.keep_last || keep.keep_hourly));
  const shown = more ? KEEPS : KEEPS.slice(0, 4);
  return (
    <span className="inline-flex flex-wrap items-end gap-2">
      {shown.map(([k, l, h]) => <label key={k} className="text-xs text-muted" title={h}>{l}<input type="number" min={1} className={input + " !w-20 mt-0.5"} value={keep[k] ?? ""} onChange={(e) => setKeep({ ...keep, [k]: e.target.value })} /></label>)}
      {!more && <button type="button" onClick={() => setMore(true)} className="text-xs text-accent hover:underline pb-2">more</button>}
    </span>
  );
}

const blankCopy = (servers: Server[]): Layer => {
  const s = servers[0];
  const st = s?.stores.find((x) => x.backend === "s3") ?? s?.stores[0];
  return { label: "Offsite copy", target_id: s?.id ?? "", store: st?.name ?? "", namespace: "", schedule: "*-*-01 03:00", what: "all", last: null, verified_only: false, remove_vanished: false, prune_schedule: "daily", gc_schedule: null, enabled: true, keep_monthly: 6 };
};

export default function BackupPlans() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [clusters, setClusters] = useState<{ id: string; name: string }[]>([]);
  const [clusterId, setClusterId] = useState("");
  const curCluster = useRef("");
  curCluster.current = clusterId;
  const [data, setData] = useState<Overview | null>(null);
  const [editing, setEditing] = useState<Plan | "new" | null>(null);
  const [pend, setPend] = useState<{ op: Operation; label: string } | null>(null);
  const [leaveOther, setLeaveOther] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { fetch("/api/defaults").then((r) => r.json()).then((d) => { setClusters(d.clusters); if (d.clusters[0]) setClusterId(d.clusters[0].id); }).catch(() => setError("Could not load clusters")); }, []);
  const load = useCallback(async () => {
    if (!clusterId) return;
    const mine = clusterId;
    const r = await fetch(`/api/backup-policies?cluster_id=${clusterId}`);
    const d = await r.json();
    if (curCluster.current !== mine) return;
    if (!r.ok) { setError(d.error || "Could not read the backup plans"); return; }
    setData(d);
  }, [clusterId]);
  useEffect(() => { load(); }, [load]);
  const hashed = useRef(false);
  useEffect(() => {  // a link from another page (#plan-<id>) opens that plan's settings once
    if (!data || hashed.current) return;
    hashed.current = true;
    const h = typeof window !== "undefined" ? window.location.hash : "";
    const p = h.startsWith("#plan-") ? data.policies.find((x) => `plan-${x.id}` === h.slice(1)) : undefined;
    if (p) { setEditing(p); window.scrollTo({ top: 0 }); }
  }, [data]);

  async function preview(change: object, label: string) {
    setBusy(true); setError(null);
    try {
      const r = await fetch("/api/backup-policies/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, change, leave_other: leaveOther }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setPend({ op: d, label }); setEditing(null); notifyOperationsChanged();
    } catch { setError("Could not reach PyXie to check that. Try again."); } finally { setBusy(false); }
  }
  if (!admin && me !== undefined) return null;
  const plans = data?.policies ?? [];
  const def = plans.find((p) => p.is_default);
  const others = plans.filter((p) => !p.is_default);
  return (
    <div className="space-y-3 mb-4">
      {clusters.length > 1 && <select className={input + " max-w-xs"} value={clusterId} onChange={(e) => { setClusterId(e.target.value); setData(null); }}>{clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
      {error && <div className="text-bad text-sm">{error}</div>}
      {pend && <Card><CardTitle>{pend.label}</CardTitle><OpPreview key={pend.op.id} op={pend.op} confirmLabel="Confirm" progressText="Changing the backup jobs" doneText="Done and read back from Proxmox and the backup server." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} /></Card>}
      <Card>
        <div className="flex items-center justify-between mb-2">
          <CardTitle>Backup plan</CardTitle>
          {!editing && !pend && data && <button type="button" onClick={() => setEditing("new")} className="px-2 py-1 rounded border border-border text-xs hover:bg-surface2">{plans.length === 0 ? "Create the plan" : "+ Plan for exceptions"}</button>}
        </div>
        {!data ? <div className="text-sm text-muted">Reading the backup jobs from Proxmox…</div> : (<>
          {data.drift.length > 0 && !pend && !editing && (
            <div className="mb-3 rounded border border-warn/50 bg-warn/10 p-2 text-xs text-warn flex flex-wrap items-center gap-3">
              <span>Proxmox differs from the plans ({data.drift.map((d) => `${d.action} ${d.job_id}`).join(", ")}): for example a guest was tagged since the last sync.</span>
              <button type="button" disabled={busy} onClick={() => preview({ action: "sync" }, "Sync the backup jobs with the plans")} className="px-2 py-1 rounded border border-warn/60 hover:bg-warn/10">Preview sync</button>
            </div>)}
          {editing ? <PlanForm key={editing === "new" ? "new" : editing.id} existing={editing === "new" ? null : editing} data={data} isFirst={plans.length === 0} busy={busy} onCancel={() => setEditing(null)}
            onPreview={(p) => preview({ action: "save", policy: p }, p.id ? `Update plan ${p.name}` : `New plan ${p.name}`)} /> : (<>
            {plans.length === 0 && <div className="text-sm text-muted">No plan yet. Create one: it becomes the default every guest follows.</div>}
            {def && <PlanView p={def} onEdit={() => setEditing(def)} onDelete={null} />}
            {others.length > 0 && <div className="mt-3 text-xs text-muted">Exceptions (a guest follows one of these when you pick it on its row below, or by tag)</div>}
            {others.map((p) => <PlanView key={p.id} p={p} onEdit={() => setEditing(p)} onDelete={p.guest_count > 0 || p.tags.length > 0 || p.set_by_hand > 0 ? "in use" : () => preview({ action: "delete", policy_id: p.id }, `Delete plan ${p.name}`)} />)}
          </>)}
          {!editing && <label className="flex items-center gap-2 text-xs mt-3 text-muted"><input type="checkbox" checked={leaveOther} onChange={(e) => setLeaveOther(e.target.checked)} /> Take guests out of other backup and sync jobs that already cover them, so nothing is backed up twice</label>}
        </>)}
      </Card>
    </div>
  );
}

function PlanView({ p, onEdit, onDelete }: { p: Plan; onEdit: () => void; onDelete: null | "in use" | (() => void) }) {
  const m = p.maintenance ?? ({} as Maint);
  const maint = [m.prune ? `prune ${m.prune}` : null, m.gc ? `free space ${m.gc}` : null, m.verify ? `verify ${m.verify}` : null].filter(Boolean).join(" · ");
  return (
    <div id={`plan-${p.id}`} className="rounded border border-border p-3 mt-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-text font-medium">{p.name}</span>
        {p.is_default ? <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-proxmox/20 text-proxmox">Default: every guest without its own plan</span>
          : <span className="text-xs text-muted">{[p.tags.length ? `tagged ${p.tags.join(", ")}` : null, p.set_by_hand ? `${p.set_by_hand} chosen by hand` : null].filter(Boolean).join("; ") || "no guest uses it yet"}</span>}
        {!p.enabled && <span className="text-warn text-xs">(switched off)</span>}
        <span className="flex-1" />
        <span className={`text-xs ${p.three_two_one.ok ? "text-good" : "text-warn"}`} title={p.three_two_one.missing.join("; ")}>{p.three_two_one.ok ? "3-2-1 met" : `3-2-1: needs ${p.three_two_one.missing.join("; ")}`}</span>
        <button type="button" onClick={onEdit} className="text-xs text-accent hover:underline">Edit</button>
        {onDelete && <button type="button" disabled={onDelete === "in use"} title={onDelete === "in use" ? "In use: give its guests another plan first" : "Delete this plan"} onClick={() => typeof onDelete === "function" && onDelete()} className={onDelete === "in use" ? "text-xs text-muted cursor-not-allowed" : "text-xs text-bad hover:underline"}>Delete</button>}
      </div>
      <ol className="mt-2 space-y-0.5 text-xs">
        <li><span className="text-text">1. Back up</span> {human(p.schedule)} to <span className="text-text">{p.storage}</span>{p.datastore && p.datastore !== p.storage ? <span className="text-muted"> ({p.datastore})</span> : null}: {keepWords(p as unknown as Record<string, unknown>)}</li>
        {p.layers.map((l, i) => <li key={i}><span className="text-text">{i + 2}. Copy</span> {human(l.schedule)} to <span className="text-text">{l.store}{l.namespace ? ` / ${l.namespace}` : ""}</span>: {l.namespace || p.is_default ? keepWords(l) : "kept by the datastore's own rules"}</li>)}
        <li className="text-muted">Maintenance: {maint || "none switched on"}</li>
      </ol>
      {p.warnings.filter((w) => w.level !== "info").map((w, i) => <div key={i} className={`text-xs mt-1 ${w.level === "bad" ? "text-bad" : "text-warn"}`}>{w.text}</div>)}
    </div>
  );
}

/** A small picture of the plan that follows every choice: guest, each place a copy lands, how often, what is kept, and whether 3-2-1 holds. */
function Diagram({ data, schedule, storage, keep, layers, prune, gc, verify }: { data: Overview; schedule: string; storage: string; keep: Record<string, string>; layers: Layer[]; prune: string; gc: string; verify: string }) {
  const first = data.storages.find((s) => s.name === storage);
  const allStores = data.servers.flatMap((s) => s.stores);
  const kindOf = (ds?: string | null) => allStores.find((x) => x.name === ds)?.backend ?? "filesystem";
  const k0 = (first?.datastore ? kindOf(first.datastore) : "filesystem");
  const keeps = (k: Record<string, unknown>) => KEEPS.filter(([x]) => k[x]).slice(0, 4).map(([x, l]) => `${k[x]} ${l.toLowerCase()}`);
  const offsite = (name: string, kind: string) => kind === "s3" || name.toLowerCase().includes("offsite");
  const places = [{ title: first?.datastore ?? storage, sub: first?.namespace ? `namespace ${first.namespace}` : "", kind: k0, offsite: offsite(first?.datastore ?? storage, k0), label: human(schedule), keeps: keeps(keep) },
    ...layers.map((l) => { const k = kindOf(l.store); return { title: l.store, sub: l.namespace ? `namespace ${l.namespace}` : "", kind: k, offsite: offsite(l.store, k), label: human(l.schedule), keeps: l.namespace || true ? keeps(l) : [] }; })];
  const copies = 1 + places.length;
  const kinds = new Set(["live", ...places.map((p) => p.kind)]);
  const off = places.some((p) => p.offsite);
  const checks: [boolean, string][] = [[copies >= 3, `${copies} cop${copies === 1 ? "y" : "ies"} (3 needed)`], [kinds.size >= 2, `${kinds.size} kind${kinds.size === 1 ? "" : "s"} of storage (2 needed)`], [off, off ? "1 offsite copy" : "no offsite copy"]];
  const maint = [prune ? `prune ${prune}` : null, gc ? `free space ${gc}` : null, verify ? `verify ${verify}` : null].filter(Boolean) as string[];
  return (
    <div className="rounded border border-border bg-surface2/30 p-3">
      <div className="flex flex-wrap items-stretch gap-2">
        <div className="rounded border border-border bg-surface px-3 py-2 text-xs flex flex-col justify-center"><div className="text-text font-medium">Guest</div><div className="text-muted">live</div></div>
        {places.map((p, i) => (
          <div key={i} className="flex items-stretch gap-2">
            <div className="flex flex-col items-center justify-center text-[11px] text-muted min-w-[5.5rem] text-center"><span>{p.label}</span><span className="text-base leading-none text-text">→</span></div>
            <div className={`rounded border px-3 py-2 text-xs ${p.offsite ? "border-accent/50" : "border-border"} bg-surface`}>
              <div className="text-text font-medium">{p.title}</div>
              <div className="text-muted">{p.kind === "s3" ? "S3 object storage" : "disk"}{p.offsite ? " · offsite" : ""}{p.sub ? ` · ${p.sub}` : ""}</div>
              <div className="flex flex-wrap gap-1 mt-1">{p.keeps.length ? p.keeps.map((k) => <span key={k} className="px-1.5 py-0.5 rounded-full bg-accent/15 text-accent">keep {k}</span>) : <span className="px-1.5 py-0.5 rounded-full bg-warn/15 text-warn">keeps everything</span>}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-xs">
        <span className="text-muted">Maintenance on each:</span>{maint.length ? maint.map((m) => <span key={m} className="px-1.5 py-0.5 rounded-full bg-surface2 text-text">{m}</span>) : <span className="text-warn">none switched on</span>}
        <span className="flex-1" />
        {checks.map(([ok, t]) => <span key={t} className={ok ? "text-good" : "text-warn"}>{ok ? "✓" : "✗"} {t}</span>)}
      </div>
    </div>
  );
}

function PlanForm({ existing, data, isFirst, busy, onCancel, onPreview }: { existing: Plan | null; data: Overview; isFirst: boolean; busy: boolean; onCancel: () => void; onPreview: (p: Record<string, unknown>) => void }) {
  const stores = data.storages;
  const [name, setName] = useState(existing?.name ?? (isFirst ? "Standard" : ""));
  const [storage, setStorage] = useState(existing?.storage ?? stores.find((s) => s.type === "pbs")?.name ?? stores[0]?.name ?? "");
  const [schedule, setSchedule] = useState(existing?.schedule ?? "02:00");
  const [keep, setKeep] = useState<Record<string, string>>(() => Object.fromEntries(KEEPS.map(([k]) => [k, String((existing as unknown as Record<string, number | null>)?.[k] ?? (!existing && k === "keep_daily" ? 14 : !existing && k === "keep_weekly" ? 4 : ""))])));
  const [layers, setLayers] = useState<Layer[]>(existing?.layers ?? (isFirst && data.servers.length ? [blankCopy(data.servers)] : []));
  const m0 = existing?.maintenance;
  const [prune, setPrune] = useState<string>(m0 ? m0.prune ?? "" : "daily");
  const [gc, setGc] = useState<string>(m0 ? m0.gc ?? "" : "sat 18:15");
  const [verify, setVerify] = useState<string>(m0 ? m0.verify ?? "" : "sun 06:00");
  const [verifyAfter, setVerifyAfter] = useState(String(m0?.verify_after ?? 30));
  const [mode, setMode] = useState(existing?.mode ?? "snapshot");
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [notes, setNotes] = useState(existing?.notes_template ?? "");
  const [tagSel, setTagSel] = useState<string[]>(existing?.tags ?? []);
  const [makeDefault, setMakeDefault] = useState(isFirst);
  const isDefault = !!existing?.is_default || makeDefault;
  const ready = name.trim() && storage && schedule;
  const policy = () => ({
    ...(existing ? { id: existing.id } : {}), name: name.trim(), storage, schedule, mode, enabled, notes_template: notes.trim() || null, tags: tagSel, make_default: makeDefault && !existing?.is_default,
    ...Object.fromEntries(KEEPS.map(([k]) => [k, keep[k] ? parseInt(keep[k], 10) : null])),
    maintenance: { prune: prune.trim() || null, gc: gc.trim() || null, verify: verify.trim() || null, verify_after: parseInt(verifyAfter, 10) || 30 },
    layers: layers.map(({ text, server, ...l }) => l),  // eslint-disable-line @typescript-eslint/no-unused-vars
  });
  const setLayer = (i: number, patch: Partial<Layer>) => setLayers(layers.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const places = data.servers.flatMap((s) => s.stores.map((st) => ({ key: `${s.id}|${st.name}`, label: `${s.name} · ${st.name} (${st.backend === "s3" ? "S3, offsite" : "disk"})`, s, st })));
  return (
    <div className="space-y-4 text-sm">
      <div className="max-w-sm"><label className="block text-xs text-muted mb-1">Plan name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Standard" /></div>
      <Diagram data={data} schedule={schedule} storage={storage} keep={keep} layers={layers} prune={prune} gc={gc} verify={verify} />

      <div className="rounded border border-border p-3 space-y-2">
        <div className="text-xs text-text font-medium">1. Back up</div>
        <div className="flex flex-wrap items-center gap-2"><When value={schedule} onChange={(v) => v !== schedule && setSchedule(v)} /><span className="text-xs text-muted">to</span>
          <select className={input + " !w-auto"} value={storage} onChange={(e) => setStorage(e.target.value)}>{stores.map((s) => <option key={s.name} value={s.name}>{s.name}{s.type === "pbs" ? ` (backup server${s.namespace ? `, namespace ${s.namespace}` : ""})` : ` (${s.type})`}</option>)}</select></div>
        <div className="flex flex-wrap items-end gap-2"><span className="text-xs text-muted pb-2">Keep</span><Keep keep={keep} setKeep={setKeep} /></div>
      </div>

      {layers.map((l, i) => {
        const server = data.servers.find((s) => s.id === l.target_id);
        const st = server?.stores.find((s) => s.name === l.store);
        return (
          <div key={l.id ?? `n${i}`} className="rounded border border-border p-3 space-y-2">
            <div className="flex items-center justify-between"><div className="text-xs text-text font-medium">{i + 2}. Copy</div><button type="button" onClick={() => setLayers(layers.filter((_, j) => j !== i))} className="text-xs text-bad hover:underline">Remove</button></div>
            <div className="flex flex-wrap items-center gap-2"><When value={l.schedule} onChange={(v) => v !== l.schedule && setLayer(i, { schedule: v })} /><span className="text-xs text-muted">to</span>
              <select className={input + " !w-auto"} value={`${l.target_id}|${l.store}`} onChange={(e) => { const [t, s] = e.target.value.split("|"); setLayer(i, { target_id: t, store: s, namespace: "" }); }}>{places.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}</select></div>
            <div className="flex flex-wrap items-end gap-2"><span className="text-xs text-muted pb-2">Keep</span>
              <Keep keep={Object.fromEntries(KEEPS.map(([k]) => [k, l[k] ? String(l[k]) : ""]))} setKeep={(k) => setLayer(i, Object.fromEntries(KEEPS.map(([key]) => [key, k[key] ? parseInt(k[key], 10) : null])))} /></div>
            {!isDefault && !l.namespace && <div className="text-xs text-warn">This plan is not the default, so a copy into the datastore itself is pruned by that datastore&apos;s own rules. Open More options and pick a namespace to give this copy its own retention.</div>}
            <details className="text-xs text-muted"><summary className="cursor-pointer hover:text-text">More options for this copy</summary>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
                <label>Name<input className={input + " mt-0.5"} value={l.label} onChange={(e) => setLayer(i, { label: e.target.value })} /></label>
                <label>Namespace (a folder in the datastore with its own retention)
                  <select className={input + " mt-0.5"} value={(st?.namespaces ?? []).includes(l.namespace) || l.namespace === "" ? l.namespace : "__new"} onChange={(e) => setLayer(i, { namespace: e.target.value === "__new" ? "new-namespace" : e.target.value })}>
                    <option value="">(the datastore itself)</option>{(st?.namespaces ?? []).map((n) => <option key={n} value={n}>{n}</option>)}<option value="__new">New namespace…</option></select>
                  {l.namespace !== "" && !(st?.namespaces ?? []).includes(l.namespace) && <input className={input + " mt-1"} value={l.namespace} onChange={(e) => setLayer(i, { namespace: e.target.value.trim() })} placeholder="name of the new namespace" />}</label>
                <label>What it copies<select className={input + " mt-0.5"} value={l.what} onChange={(e) => setLayer(i, { what: e.target.value as "all" | "last", last: e.target.value === "last" ? l.last ?? 1 : null })}><option value="all">every new backup</option><option value="last">only the newest N of each guest</option></select>
                  {l.what === "last" && <input type="number" min={1} className={input + " mt-1"} value={l.last ?? ""} onChange={(e) => setLayer(i, { last: e.target.value ? parseInt(e.target.value, 10) : null })} />}</label>
              </div>
              <div className="mt-2 space-y-1">
                <label className="flex items-center gap-2"><input type="checkbox" checked={l.verified_only} onChange={(e) => setLayer(i, { verified_only: e.target.checked })} /> Only copy backups that passed verification</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={l.remove_vanished} onChange={(e) => setLayer(i, { remove_vanished: e.target.checked })} /> Remove a copy when the original is pruned (leave off to keep copies longer than the originals)</label>
              </div>
              {server && !server.has_remote && <div className="text-warn mt-1">This backup server has no remote pointing at itself, which a copy needs (PBS: Configuration, Remotes, host 127.0.0.1).</div>}
            </details>
          </div>
        );
      })}
      <button type="button" disabled={data.servers.length === 0} onClick={() => setLayers([...layers, { ...blankCopy(data.servers), label: `Copy ${layers.length + 1}` }])} className="px-2.5 py-1 rounded border border-border text-xs hover:bg-surface2 disabled:opacity-50">+ Add a copy</button>
      {data.servers.length === 0 && <span className="text-xs text-warn ml-2">No backup server with a write token is saved (Settings, Credentials), so copies cannot be planned.</span>}

      <div className="rounded border border-border p-3 space-y-2">
        <div className="text-xs text-text font-medium">Maintenance (applies to every place this plan writes to)</div>
        <MaintRow label="Prune" help="Deletes backups that are outside the keep rules above." value={prune} onChange={setPrune} fallback="daily" />
        <MaintRow label="Free the space (garbage collection)" help="Removes data no backup uses any more. One schedule per datastore, shared by every plan on it." value={gc} onChange={setGc} fallback="sat 18:15" />
        <MaintRow label="Verify" help="Re-reads backups and checks them against their checksums." value={verify} onChange={setVerify} fallback="sun 06:00"
          extra={verify ? <span className="text-xs text-muted">check each backup again after <input type="number" min={1} className={input + " !w-16 mx-1"} value={verifyAfter} onChange={(e) => setVerifyAfter(e.target.value)} /> days</span> : null} />
        <div className="text-xs text-muted">Every backup here is complete on its own and shares unchanged data with the others, so there is no full or incremental to manage: keep 14 daily and 4 weekly keeps the newest backup of each of those days and weeks (the two overlap a little), and pruning never breaks a restore point.</div>
      </div>

      <details className="text-xs text-muted"><summary className="cursor-pointer hover:text-text">More options (who gets this plan, mode, notes)</summary>
        <div className="mt-2 space-y-2">
          {existing?.is_default ? <div className="text-text">This is the default plan: every guest without another plan follows it.</div> : <label className="flex items-center gap-2"><input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} /> Make this the default plan (what every guest follows unless set differently)</label>}
          <div className="flex flex-wrap items-center gap-3"><span>Also every guest tagged:</span>{data.tags.map((t) => <label key={t} className="flex items-center gap-1"><input type="checkbox" checked={tagSel.includes(t)} onChange={(e) => setTagSel(e.target.checked ? [...tagSel, t] : tagSel.filter((x) => x !== t))} />{t}</label>)}{data.tags.length === 0 && <span>(no tags in use)</span>}</div>
          <div>To give single guests this plan, pick it on the guest&apos;s row below.</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <label>Mode<select className={input + " mt-0.5"} value={mode} onChange={(e) => setMode(e.target.value)}>{MODES.map((x) => <option key={x.v} value={x.v}>{x.l}</option>)}</select></label>
            <label>Note on each backup<input className={input + " mt-0.5"} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="{{guestname}}" /></label>
            <label className="flex items-center gap-2 pt-5"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled</label>
          </div>
        </div>
      </details>

      <div className="flex gap-2 items-center">
        <button type="button" disabled={busy || !ready} onClick={() => onPreview(policy())} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{busy ? "Checking…" : "Preview"}</button>
        <button type="button" onClick={onCancel} className="text-xs text-muted hover:text-text">cancel</button>
        <span className="text-xs text-muted">Nothing changes until you confirm the preview, which lists every job on Proxmox and on the backup server.</span>
      </div>
    </div>
  );
}

function MaintRow({ label, help, value, onChange, fallback, extra }: { label: string; help: string; value: string; onChange: (v: string) => void; fallback: string; extra?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <label className="flex items-center gap-2 w-64 text-xs text-text"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked ? fallback : "")} />{label}</label>
      {value ? <input className={input + " !w-44"} value={value} onChange={(e) => onChange(e.target.value)} /> : <span className="text-xs text-muted w-44">off (left as it is)</span>}
      {extra}
      <span className="text-xs text-muted">{help}</span>
    </div>
  );
}
