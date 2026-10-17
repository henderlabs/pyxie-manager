"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Row = { ns: string; guests: number; backups: number; used_by: string[]; children: number };
type Store = { name: string; backend: string; gc_schedule: string | null; namespaces: Row[] };
type Server = { id: string; name: string; can_write: boolean; error: string | null; stores: Store[] };
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm";
const btn = "px-2.5 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs";

/** Namespaces of every backup server datastore: a folder inside a datastore that shares its stored data but keeps its own backups, jobs and retention. */
export default function PbsNamespaces() {
  const me = useMe();
  const canEdit = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<Server[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pend, setPend] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [name, setName] = useState("");
  const load = useCallback(async () => {
    try { const r = await fetch("/api/pbs/namespaces"); const d = await r.json(); if (!r.ok) throw new Error(d.error || "Could not read the backup servers"); setData(d); } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function addStorage(serverId: string, store: string, ns: string, purpose: "backup" | "restore") {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/pbs/servers/${serverId}/storage-add/dry-run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ datastore: store, namespace: ns, purpose }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Could not check that");
      setPend(d); notifyOperationsChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function run(serverId: string, store: string, action: "ns_create" | "ns_delete", ns: string) {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/pbs/servers/${serverId}/datastores/dry-run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ store, action, params: { ns } }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Could not check that");
      setPend(d); setAdding(null); setName(""); notifyOperationsChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (pend) return <Card><OpPreview key={pend.id} op={pend} confirmLabel="Confirm" progressText="Changing the backup server" doneText="Done and read back from the backup server." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} /></Card>;
  return (
    <div className="space-y-4">
      <Card>
        <p className="text-sm text-muted">A <b className="text-text">namespace</b> is a folder inside a datastore (an NFS share or an S3 bucket on the backup server). It shares the datastore&apos;s stored data, so a backup that exists in two namespaces is stored once, but each namespace has its own list of backups and its own pruning. That is how two policies keep different retention in the same datastore: a policy&apos;s copy goes into a namespace of its own on the
          <Link href="/operations/protection" className="text-accent hover:underline mx-1">backup plan</Link>
          page, or the policy first backup can be sent to one (&quot;Use for backups…&quot; adds it to Proxmox as a backup storage first). Creating a namespace costs nothing and deletes nothing. PyXie only deletes a namespace that is empty and that nothing uses. Garbage collection is one schedule per datastore and covers all its namespaces.</p>
      </Card>
      {err && <div className="text-bad text-sm">{err}</div>}
      {!data && !err && <div className="text-sm text-muted">Reading the backup servers…</div>}
      {(data ?? []).map((s) => (
        <div key={s.id} className="space-y-3">
          {s.error && <div className="text-warn text-sm">{s.name}: {s.error}</div>}
          {!s.can_write && canEdit && <div className="text-warn text-xs">{s.name} is read-only here until a write token is saved (Settings, Credentials).</div>}
          {s.stores.map((st) => (
            <Card key={st.name}>
              <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                <div><CardTitle>{st.name}</CardTitle><div className="text-xs text-muted -mt-1">{s.name} · {st.backend === "s3" ? "S3 object storage (offsite)" : "disk"} · garbage collection {st.gc_schedule ?? "has no schedule"}</div></div>
                {canEdit && s.can_write && adding !== `${s.id}:${st.name}` && <button className={btn} onClick={() => { setAdding(`${s.id}:${st.name}`); setName(""); }}>New namespace</button>}
              </div>
              {adding === `${s.id}:${st.name}` && (
                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <input className={input} value={name} placeholder="e.g. short-lived or customers/acme" onChange={(e) => setName(e.target.value.trim())} />
                  <button className={btn} disabled={busy || !name} onClick={() => run(s.id, st.name, "ns_create", name)}>{busy ? "Checking…" : "Preview"}</button>
                  <button className="text-xs text-muted" onClick={() => setAdding(null)}>cancel</button>
                  <span className="text-xs text-muted">Up to 7 levels with /. The parent must exist.</span>
                </div>
              )}
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Namespace</th><th className="pr-3">Guests</th><th className="pr-3">Backups</th><th className="pr-3">Used by</th><th /></tr></thead>
                <tbody>
                  {st.namespaces.map((n) => {
                    const blocked = n.guests > 0 || n.used_by.length > 0 || n.children > 0;
                    return (
                      <tr key={n.ns || "(root)"} className="border-t border-border align-top">
                        <td className="py-1.5 pr-3 text-text">{n.ns || <span className="text-muted">(the datastore itself)</span>}</td>
                        <td className="pr-3">{n.guests}</td><td className="pr-3">{n.backups}</td>
                        <td className="pr-3 text-xs">{n.used_by.length ? n.used_by.map((u, i) => <div key={i}>{u}</div>) : <span className="text-muted">nothing</span>}</td>
                        <td className="text-right text-xs whitespace-nowrap">{canEdit && s.can_write && <button disabled={busy} className="text-accent hover:underline mr-3" title="Add this namespace to Proxmox as a backup storage, so a policy can send its first backups here (needs the backup token)" onClick={() => addStorage(s.id, st.name, n.ns, "backup")}>Use for backups…</button>}{n.ns && canEdit && s.can_write && <button disabled={blocked || busy} title={blocked ? "Only an empty namespace that nothing uses can be deleted" : "Delete this empty namespace"} onClick={() => run(s.id, st.name, "ns_delete", n.ns)} className={blocked ? "text-muted cursor-not-allowed" : "text-bad hover:underline"}>Delete</button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Card>
          ))}
        </div>
      ))}
    </div>
  );
}
