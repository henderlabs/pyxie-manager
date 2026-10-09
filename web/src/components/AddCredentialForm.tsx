"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

// The purposes PyXie uses. The Proxmox account script creates a token whose id equals the purpose name, so the token id is
// pre-filled to match; the user names are the defaults the script builder proposes (yours may differ).
const ALL_SLOTS = ["inventory", "maintenance", "console"];
const DEFAULT_USER: Record<string, string> = { inventory: "pyxie-ro@pve", maintenance: "pyxie-admin@pve", console: "pyxie-console@pve" };

export default function AddCredentialForm({ targetId, existingSlots }: { targetId: string; existingSlots: string[] }) {
  const router = useRouter();
  const available = ALL_SLOTS.filter((s) => !existingSlots.includes(s));
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    slot_name: available[0] ?? "",
    token_user: DEFAULT_USER[available[0] ?? ""] ?? "",
    token_id: available[0] ?? "",
    token_secret: "",
  });
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/pve-targets/${targetId}/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (available.length === 0) return null;

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-text border border-border hover:bg-surface2"
      >
        + Add Credential Purpose
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="bg-surface2 border border-border rounded-lg p-4 space-y-3 max-w-lg">
      <div className="text-sm font-semibold text-text">New Credential Purpose</div>
      <label className="block text-xs text-muted space-y-1">
        <span>Purpose (access level)</span>
        <select
          className="input"
          value={form.slot_name}
          onChange={(e) => setForm({ ...form, slot_name: e.target.value, token_user: DEFAULT_USER[e.target.value] ?? "", token_id: e.target.value })}
        >
          {available.map((s) => (
            <option key={s} value={s}>
              {s === "maintenance" ? "maintenance (admin)" : s}
            </option>
          ))}
        </select>
      </label>
      <div className="text-xs text-muted">
        {form.slot_name === "inventory"
          ? "Read-only -- only used to discover and monitor this target. Same purpose as the token used when connecting the cluster in the Setup guide."
          : form.slot_name === "maintenance"
          ? "Required for any write action (migration, maintenance, rightsizing apply, etc). Do not use root@pam."
          : "Only for the embedded VM console: a token holding just VM.Console (plus VM.Audit) so a leak cannot power off or migrate anything. Not needed if VM.Console is part of the admin (maintenance) role."}
        {" "}Use the user name and token id exactly as the Proxmox account script created them (it names the token after its purpose); the secret is in /root/pyxie-tokens.txt on the node you ran it on.
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-muted space-y-1">
          <span>Token user (user@realm)</span>
          <input
            required
            className="input"
            value={form.token_user}
            onChange={(e) => setForm({ ...form, token_user: e.target.value })}
            placeholder="pyxie-manager@pve"
          />
        </label>
        <label className="block text-xs text-muted space-y-1">
          <span>Token ID</span>
          <input
            required
            className="input"
            value={form.token_id}
            onChange={(e) => setForm({ ...form, token_id: e.target.value })}
            placeholder={form.slot_name}
          />
        </label>
      </div>
      <label className="block text-xs text-muted space-y-1">
        <span>Token secret</span>
        <input
          required
          type="password"
          className="input"
          value={form.token_secret}
          onChange={(e) => setForm({ ...form, token_secret: e.target.value })}
        />
      </label>
      {error && <div className="text-xs text-bad">{error}</div>}
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={pending}
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save credential"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
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
    </form>
  );
}
