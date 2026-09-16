"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { PveTarget } from "@/lib/api";
import ActionButton from "@/components/ActionButton";
import { useMe } from "@/lib/useMe";

export default function PveTargetRow({ target }: { target: PveTarget }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    name: target.name,
    hostname: target.hostname,
    api_port: target.api_port,
    tls_verify: target.tls_verify,
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function save() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/pve-targets/${target.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setEditing(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (editing) {
    return (
      <div className="bg-surface2 rounded p-3 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-xs text-muted space-y-1">
            <span>Name</span>
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="block text-xs text-muted space-y-1">
            <span>Hostname / IP</span>
            <input
              className="input"
              value={form.hostname}
              onChange={(e) => setForm({ ...form, hostname: e.target.value })}
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3 items-end">
          <label className="block text-xs text-muted space-y-1">
            <span>API Port</span>
            <input
              type="number"
              className="input"
              value={form.api_port}
              onChange={(e) => setForm({ ...form, api_port: Number(e.target.value) })}
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-text pb-1.5">
            <input
              type="checkbox"
              checked={form.tls_verify}
              onChange={(e) => setForm({ ...form, tls_verify: e.target.checked })}
            />
            Verify TLS certificate
          </label>
        </div>
        {error && <div className="text-xs text-bad">{error}</div>}
        <div className="flex gap-2">
          <button
            onClick={save}
            disabled={pending}
            className="px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => {
              setEditing(false);
              setForm({ name: target.name, hostname: target.hostname, api_port: target.api_port, tls_verify: target.tls_verify });
            }}
            className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-muted border border-border"
          >
            Cancel
          </button>
        </div>
        <style jsx>{`
          .input {
            width: 100%;
            background: #0b0e14;
            border: 1px solid #232a38;
            border-radius: 6px;
            padding: 6px 8px;
            font-size: 0.875rem;
            color: #e6e9ef;
          }
        `}</style>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between bg-surface2 rounded p-3">
      <div>
        <div className="text-sm text-text">{target.name}</div>
        <div className="text-xs text-muted">
          {target.hostname}:{target.api_port} · TLS {target.tls_verify ? "verified" : "not verified"}
        </div>
      </div>
      <div className="flex gap-2">
        {isAdmin && (
          <button
            onClick={() => setEditing(true)}
            className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-text border border-border hover:bg-surface2/70"
          >
            Edit
          </button>
        )}
        <ActionButton href={`/api/pve-targets/${target.id}/test-connection`} label="Test Connection" />
        <ActionButton href={`/api/pve-targets/${target.id}/discover`} label="Sync Now" variant="primary" />
      </div>
    </div>
  );
}
