"use client";

import { useState } from "react";
import { useMe } from "@/lib/useMe";
import StatusBadge from "@/components/StatusBadge";

export type PbsToken = { id: string; token_user: string; token_id: string; masked_secret: string; status: string; last_validated_at: string | null };
type Check = { check: string; ok: boolean; detail: unknown };

const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const btn = "px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs";
export const PBS_SLOT_INFO: Record<string, { label: string; user: string; help: string }> = {
  restore: { label: "Restore (read data)", user: "pyxie-pbs-restore@pbs", help: "Read-only access to backup data. Used only to add a datastore to Proxmox as a backup storage so you can restore from it (for example the offsite copy). It cannot write or delete backups." },
  backup: { label: "Backup (write backups)", user: "pyxie-pbs-backup@pbs", help: "Lets Proxmox back guests up into a namespace of a datastore, and prune its own backups there (DatastorePowerUser). Used only when you add a datastore or namespace to Proxmox as a backup target; the datastore Proxmox already uses keeps its own login." },
  inventory: { label: "Read-only (inventory)", user: "pyxie-pbs-ro@pbs", help: "Watches the server: status, datastores, tasks, jobs." },
  administrative: { label: "Admin (administrative)", user: "pyxie-pbs-admin@pbs", help: "Creates, changes, deletes and runs sync, prune and verify jobs. Never installs packages." },
};

async function call(method: string, path: string, body?: unknown) {
  const r = await fetch(`/api/pbs/${path}`, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
  return d;
}

/** One PBS API token (a "slot") on one backup server: view, add, edit, delete, test. Used on the Credentials page and on the backup server's Access tab. */
export default function PbsTokenSlot({ serverId, slot, token, onChanged }: { serverId: string; slot: "inventory" | "administrative" | "restore" | "backup"; token?: PbsToken; onChanged: () => void }) {
  const info = PBS_SLOT_INFO[slot];
  const me = useMe();
  const canEdit = me === undefined || me?.is_admin === true;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ user: token?.token_user ?? info.user, id: token?.token_id ?? slot, secret: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [confirmDel, setConfirmDel] = useState(false);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<void>) => { setBusy(true); setMsg(null); try { await fn(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); } };
  const save = () => run(async () => { await call("PUT", `servers/${serverId}/credentials/${slot}`, { token_user: form.user, token_id: form.id, token_secret: form.secret }); setEditing(false); setForm({ ...form, secret: "" }); onChanged(); });
  const test = () => run(async () => { const d = await call("POST", `servers/${serverId}/test-access?slot=${slot}`); setChecks(d.checks); onChanged(); });
  const del = () => run(async () => { await call("DELETE", `servers/${serverId}/credentials/${slot}`); setConfirmDel(false); setChecks([]); onChanged(); });
  return (
    <div className="py-2 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-text font-medium">{slot}</div>
          <div className="text-xs text-muted">{token ? `${token.token_user}!${token.token_id}` : "not set"}</div>
        </div>
        {token && <div className="text-xs text-muted font-mono">{token.masked_secret}</div>}
        {token && <StatusBadge status={token.status} />}
        {token && <div className="text-xs text-muted">{token.last_validated_at ? `validated ${new Date(token.last_validated_at).toLocaleString()}` : "never validated"}</div>}
        {token && canEdit && <button className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-text border border-border hover:bg-surface2 disabled:opacity-50" disabled={busy} onClick={test}>Test Connection</button>}
      </div>
      <div className="text-xs text-muted">{info.help}</div>
      <div className="flex items-start gap-3 mt-1">
        {token && canEdit && <button onClick={() => setEditing(!editing)} className="text-xs text-accent hover:underline">Edit</button>}
        {token && canEdit && slot !== "inventory" && (confirmDel
          ? <span className="inline-flex items-center gap-2 text-xs"><span className="text-muted">Delete the “{slot}” credential and its stored secret? This does not touch the PBS.</span><button className="px-2 py-1 rounded font-medium bg-bad/10 text-bad border border-bad disabled:opacity-50" disabled={busy} onClick={del}>Yes, delete</button><button className="px-2 py-1 rounded bg-surface text-muted border border-border" onClick={() => setConfirmDel(false)}>Cancel</button></span>
          : <button onClick={() => setConfirmDel(true)} className="text-xs text-bad hover:underline">Delete</button>)}
        {!token && canEdit && <button onClick={() => setEditing(true)} className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-text border border-border hover:bg-surface2">+ Add Credential</button>}
      </div>
      {editing && canEdit && (
        <div className="bg-surface2 border border-border rounded p-3 mt-2 space-y-2 max-w-2xl">
          <div className="text-xs text-muted">{token ? "Leave the secret empty to keep the saved one. A new secret replaces it; the old one cannot be recovered." : "The secret is shown once when the token is created on the PBS. It is stored encrypted and never shown again."}</div>
          <div className="grid grid-cols-3 gap-2">
            <label className="text-xs text-muted">User (user@realm)<input className={input} value={form.user} onChange={(e) => setForm({ ...form, user: e.target.value })} /></label>
            <label className="text-xs text-muted">Token name<input className={input} value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} /></label>
            <label className="text-xs text-muted">Secret<input className={input} type="password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} /></label>
          </div>
          <div className="flex gap-2"><button className={btn} disabled={busy || (!token && !form.secret)} onClick={save}>{busy ? "Saving…" : "Save"}</button><button className="text-xs text-muted" onClick={() => setEditing(false)}>Cancel</button></div>
        </div>
      )}
      {msg && <div className="text-xs text-bad mt-1">{msg}</div>}
      {checks.map((c) => <div key={c.check} className={`text-xs mt-0.5 ${c.ok ? "text-good" : "text-bad"}`}>{c.ok ? "✓" : "✗"} {c.check}: <span className="text-muted">{typeof c.detail === "string" ? c.detail : JSON.stringify(c.detail)}</span></div>)}
    </div>
  );
}
