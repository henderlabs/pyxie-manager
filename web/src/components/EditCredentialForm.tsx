"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Credential } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function EditCredentialForm({ targetId, credential }: { targetId: string; credential: Credential }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    token_user: credential.token_user,
    token_id: credential.token_id,
    token_secret: "",
  });
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function save() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/pve-targets/${targetId}/credentials/${credential.id}`, {
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

  if (!editing) {
    return (
      <button onClick={() => setEditing(true)} className="text-xs text-accent hover:underline">
        Edit
      </button>
    );
  }

  return (
    <div className="bg-surface2 border border-border rounded-lg p-3 space-y-2 mt-2">
      <div className="text-xs text-muted">
        Rotating this key replaces its token material entirely -- the current secret can't be recovered, and the slot
        will show as untested until it's used again.
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block text-xs text-muted space-y-1">
          <span>Token user (user@realm)</span>
          <input
            className="input"
            value={form.token_user}
            onChange={(e) => setForm({ ...form, token_user: e.target.value })}
          />
        </label>
        <label className="block text-xs text-muted space-y-1">
          <span>Token ID</span>
          <input className="input" value={form.token_id} onChange={(e) => setForm({ ...form, token_id: e.target.value })} />
        </label>
      </div>
      <label className="block text-xs text-muted space-y-1">
        <span>New token secret</span>
        <input
          type="password"
          className="input"
          value={form.token_secret}
          onChange={(e) => setForm({ ...form, token_secret: e.target.value })}
          placeholder="required to rotate"
        />
      </label>
      {error && <div className="text-xs text-bad">{error}</div>}
      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={pending || !form.token_secret}
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={() => {
            setEditing(false);
            setForm({ token_user: credential.token_user, token_id: credential.token_id, token_secret: "" });
            setError(null);
          }}
          className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-muted border border-border"
        >
          Cancel
        </button>
      </div>
      <style jsx>{`
        .input {
          width: 100%;
          background: rgb(var(--c-canvas));
          border: 1px solid rgb(var(--c-border));
          border-radius: 6px;
          padding: 6px 8px;
          font-size: 0.875rem;
          color: rgb(var(--c-text));
        }
      `}</style>
    </div>
  );
}
