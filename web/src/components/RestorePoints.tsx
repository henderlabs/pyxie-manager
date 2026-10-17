"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Point = {
  datastore: string; namespace?: string; time: number; size: number | null; verified: string | null | undefined; protected: boolean; notes?: string | null;
  offsite?: boolean; server_id?: string; volid: string | null; storage: string | null; restorable: boolean; reason: string | null;
};
type Options = {
  kind: "vm" | "ct"; name: string; node: string; status: string; next_id: number | null; error?: string;
  nodes: { name: string; online: boolean; maintenance: boolean }[]; storages: { name: string; avail: number | null; type: string }[]; pools: string[];
};

const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const btn = "px-3 py-1.5 rounded bg-ink text-on-ink border hover:bg-proxmox/10 disabled:opacity-50 text-xs";
const size = (b: number | null) => (b ? (b >= GIB ? `${(b / GIB).toFixed(1)} GiB` : `${Math.max(1, Math.round(b / 1048576))} MiB`) : "—");
const when = (s: number) => new Date(s * 1000).toLocaleString();

async function call(method: string, path: string, body?: unknown) {
  const r = await fetch(`/api/restore/${path}`, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
  return d;
}

/** Every restore point of one guest (on-site and offsite), and the restore form for the one you pick. */
export default function RestorePoints({ workloadId, name, vmid }: { workloadId: string; name: string; vmid: number }) {
  const me = useMe();
  const canRestore = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<{ points: Point[]; errors: string[]; kind: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<Point | null>(null);
  const [act, setAct] = useState<{ op: Operation; label: string; reload: boolean } | null>(null);
  const [actErr, setActErr] = useState<string | null>(null);
  async function pbsAction(p: Point, path: string, body: unknown, label: string, reload: boolean) {
    setActErr(null);
    try {
      const r = await fetch(`/api/pbs/servers/${p.server_id}/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Preview failed");
      setAct({ op: d, label, reload }); notifyOperationsChanged();
    } catch (e) { setActErr((e as Error).message); }
  }
  useEffect(() => { call("GET", `points/${workloadId}`).then(setData).catch((e) => setErr(e.message)); }, [workloadId]);
  if (err) return <div className="text-bad text-sm p-3">{err}</div>;
  if (!data) return <div className="text-muted text-sm p-3">Reading the restore points of {name}…</div>;
  return (
    <div className="p-3 space-y-3">
      <div className="text-sm text-text font-medium">Restore points of {name} <span className="text-muted font-normal">(ID {vmid})</span></div>
      {data.errors.map((e) => <div key={e} className="text-warn text-xs">{e}</div>)}
      {actErr && <div className="text-bad text-xs">{actErr}</div>}
      {act && <OpPreview key={act.op.id} op={act.op} confirmLabel={act.label} progressText="Working on the backup server" doneText="Done and read back from the backup server (runs such as verify continue there and can take a while)." onFinished={() => notifyOperationsChanged()} onClose={() => { const r = act.reload; setAct(null); if (r) window.location.reload(); }} />}
      {pick && <RestoreForm workloadId={workloadId} name={name} point={pick} onDone={() => setPick(null)} />}
      <div className="text-xs text-muted">{data.points.length} restore point(s), newest first. Offsite copies can be restored once Proxmox has a storage for that datastore.</div>
      {data.points.length === 0 ? <div className="text-sm text-muted">No backups of {name} (ID {vmid}) were found on any backup datastore.</div> : (
        <div className="max-h-[24rem] overflow-y-auto"><table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface"><tr className="text-left text-xs text-muted"><th className="py-1 pr-3">Taken</th><th className="pr-3">Datastore</th><th className="pr-3">Size</th><th className="pr-3">Verified</th><th className="pr-3">Notes</th><th></th></tr></thead>
          <tbody className="divide-y divide-border">
            {data.points.map((p) => (
              <tr key={p.datastore + (p.namespace ?? "") + p.time} className={pick === p ? "bg-accent/5" : ""}>
                <td className="py-1.5 pr-3 whitespace-nowrap">{when(p.time)}{p.protected && <span className="text-xs text-muted ml-1">(protected)</span>}</td>
                <td className="pr-3"><span className="text-text">{p.datastore}{p.namespace ? <span className="text-muted"> / {p.namespace}</span> : null}</span> <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${p.offsite ? "bg-accent/20 text-accent" : "bg-surface2 text-muted"}`}>{p.offsite ? "offsite" : "on site"}</span></td>
                <td className="pr-3">{size(p.size)}</td>
                <td className={`pr-3 ${p.verified === "ok" ? "text-good" : p.verified === "failed" ? "text-bad" : "text-muted"}`}>{p.verified === "ok" ? "verified" : p.verified === "failed" ? "FAILED" : "not verified"}</td>
                <td className="pr-3 text-xs text-muted">{p.notes ?? ""}</td>
                <td className="text-right whitespace-nowrap space-x-1">
                  {canRestore && p.server_id && <button className="text-xs text-accent hover:underline" title="Have the backup server re-read this backup and check it against its checksums"
                    onClick={() => pbsAction(p, "datastores/dry-run", { store: p.datastore, action: "verify_run", params: { backup_type: data.kind, backup_id: String(vmid), backup_time: p.time, ns: p.namespace ?? "" } }, "Verify now", false)}>Verify</button>}
                  {canRestore && p.server_id && <button className="text-xs text-accent hover:underline" title="A protected backup is skipped by pruning and cannot be deleted by mistake"
                    onClick={() => pbsAction(p, "datastores/dry-run", { store: p.datastore, action: "protect", params: { backup_type: data.kind, backup_id: String(vmid), backup_time: p.time, ns: p.namespace ?? "", protected: !p.protected } }, p.protected ? "Remove protection" : "Protect", true)}>{p.protected ? "Unprotect" : "Protect"}</button>}
                  {p.restorable ? (canRestore && <button className={`${btn} border-proxmox`} onClick={() => setPick(pick === p ? null : p)}>{pick === p ? "Close" : "Restore…"}</button>)
                    : (canRestore && p.server_id
                      ? <button className={`${btn} border-accent`} title={p.reason ?? ""} onClick={() => pbsAction(p, "storage-add/dry-run", { workload_id: workloadId, datastore: p.datastore, namespace: p.namespace ?? "" }, "Add to Proxmox", true)}>Make restorable…</button>
                      : <span className="text-xs text-muted" title={p.reason ?? ""}>not restorable from here</span>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

function RestoreForm({ workloadId, name, point, onDone }: { workloadId: string; name: string; point: Point; onDone: () => void }) {
  const [opts, setOpts] = useState<Options | null>(null);
  const [mode, setMode] = useState<"new" | "overwrite">("new");
  const [node, setNode] = useState("");
  const [newName, setNewName] = useState(`${name}-restored`);
  const [newId, setNewId] = useState("");
  const [storage, setStorage] = useState("");
  const [pool, setPool] = useState("");
  const [connected, setConnected] = useState(false);
  const [start, setStart] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [pend, setPend] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { call("GET", `options/${workloadId}${node ? `?node=${encodeURIComponent(node)}` : ""}`).then((o: Options) => { setOpts(o); if (!node) setNode(o.node); }).catch((e) => setErr(e.message)); }, [workloadId, node]);
  async function preview() {
    setBusy(true); setErr(null);
    try {
      const d = await call("POST", "dry-run", { workload_id: workloadId, volid: point.volid, mode, newid: newId ? Number(newId) : null, name: mode === "new" ? newName : null, node: mode === "new" ? node : null,
        storage: storage || null, pool: mode === "new" ? pool || null : null, start_after: start, nics_connected: connected, confirm_name: mode === "overwrite" ? confirm : null });
      setPend(d); notifyOperationsChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (pend) return <OpPreview key={pend.id} op={pend} danger={mode === "overwrite"} confirmLabel={mode === "overwrite" ? "Overwrite the guest" : "Restore"} progressText="Restoring from the backup" doneText={mode === "overwrite" ? "Restored over the guest and read back from Proxmox. Proxmox may have kept the replaced disks as unused disks of the guest: check its hardware settings and delete them to free the space." : "Restored, configured and read back from Proxmox. The guest list updates in a minute."} onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); onDone(); }} />;
  if (!opts) return <div className="text-sm text-muted">{err ?? "Reading what the restore can use…"}</div>;
  const ct = opts.kind === "ct";
  const gname = ct ? "container" : "VM";
  return (
    <div className="border border-border rounded p-3 bg-surface2/40 space-y-3 text-sm">
      <div className="text-text font-medium">Restore the backup of {when(point.time)} from {point.datastore}{point.namespace ? ` / ${point.namespace}` : ""}</div>
      <div className="flex gap-4 flex-wrap">
        <label className="flex items-center gap-2"><input type="radio" checked={mode === "new"} onChange={() => setMode("new")} />Restore as a new {gname}</label>
        <label className="flex items-center gap-2"><input type="radio" checked={mode === "overwrite"} onChange={() => setMode("overwrite")} /><span className="text-bad">Overwrite {name}</span></label>
      </div>
      {mode === "new" ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label className="text-xs text-muted">Name<input className={input} value={newName} onChange={(e) => setNewName(e.target.value)} /></label>
          <label className="text-xs text-muted">ID (empty = next free{opts.next_id ? `, ${opts.next_id}` : ""})<input className={input} type="number" value={newId} onChange={(e) => setNewId(e.target.value)} placeholder={String(opts.next_id ?? "")} /></label>
          <label className="text-xs text-muted">Host<select className={input} value={node} onChange={(e) => { setNode(e.target.value); setStorage(""); }}>{opts.nodes.filter((n) => n.online).map((n) => <option key={n.name} value={n.name}>{n.name}{n.maintenance ? " (maintenance)" : ""}</option>)}</select></label>
          <label className="text-xs text-muted">Disks go to<select className={input} value={storage} onChange={(e) => setStorage(e.target.value)}><option value="">{ct ? "the storage of its root disk in the backup" : "the storages named in the backup"}</option>{opts.storages.map((s) => <option key={s.name} value={s.name}>{s.name}{s.avail ? ` · ${(s.avail / GIB).toFixed(0)} GiB free` : ""}</option>)}</select></label>
          <label className="text-xs text-muted">Pool<select className={input} value={pool} onChange={(e) => setPool(e.target.value)}><option value="">none</option>{opts.pools.map((p) => <option key={p}>{p}</option>)}</select></label>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-xs text-bad">The current disks and snapshots of {name} are destroyed and replaced by this backup. This cannot be undone. {opts.status !== "stopped" && <b>The {gname} is {opts.status}: shut it down first.</b>}</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <label className="text-xs text-muted">Type the name to confirm ({name})<input className={input} value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
            <label className="text-xs text-muted">Disks go to<select className={input} value={storage} onChange={(e) => setStorage(e.target.value)}><option value="">the storages named in the backup</option>{opts.storages.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}</select></label>
          </div>
        </div>
      )}
      <div className="space-y-1">
        <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={connected} onChange={(e) => setConnected(e.target.checked)} /><span>Connect the network devices<span className="block text-xs text-muted">Off (default): they are restored disconnected, so the restored {gname} cannot clash with the original on the network. Connect them later in its hardware settings.</span></span></label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} />Start it after the restore</label>
      </div>
      {err && <div className="text-bad text-xs">{err}</div>}
      <div className="flex gap-2"><button className={`${btn} border-proxmox`} disabled={busy || (mode === "overwrite" && confirm !== name)} onClick={preview}>{busy ? "Checking…" : "Preview"}</button><button className="text-xs text-muted hover:text-text" onClick={onDone}>cancel</button></div>
      <p className="text-xs text-muted">Nothing changes until you confirm the preview. A new guest gets new hardware addresses, the tag <code>restored</code> and a note naming the backup.</p>
    </div>
  );
}
