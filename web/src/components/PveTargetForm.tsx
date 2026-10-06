"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Site } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function PveTargetForm({ sites }: { sites: Site[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    site_id: sites[0]?.id ?? "",
    name: "",
    hostname: "",
    api_port: 8006,
    tls_verify: true,
    token_user: "",
    token_id: "",
    token_secret: "",
  });
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/pve-targets", {
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

  if (sites.length === 0) {
    return <div className="text-sm text-muted">Add a site before connecting a PVE target.</div>;
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-white border border-accent hover:bg-accent/10"
      >
        + Add PVE Target
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="bg-surface2 border border-border rounded-lg p-4 space-y-3 max-w-lg">
      <div className="text-sm font-semibold text-text">New PVE Target</div>
      <div className="text-xs text-muted -mt-1">
        Connect one Proxmox cluster or standalone host's API endpoint. The token below only needs to read inventory --
        add a higher-access credential purpose afterward on the Credentials page if this target needs to run actions.
      </div>
      {sites.length > 1 && (
        <Field label="Site">
          <select className="input" value={form.site_id} onChange={(e) => update("site_id", e.target.value)}>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Name">
        <input
          required
          value={form.name}
          onChange={(e) => update("name", e.target.value)}
          className="input"
          placeholder="e.g. Main Cluster"
        />
      </Field>
      <div className="text-xs text-muted -mt-2">
        Your own label for this connection -- not required to match the PVE cluster's real name, which PyXie
        auto-detects on first sync.
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Hostname / IP">
          <input
            required
            value={form.hostname}
            onChange={(e) => update("hostname", e.target.value)}
            className="input"
            placeholder="192.168.1.10"
          />
        </Field>
        <Field label="API Port">
          <input
            type="number"
            value={form.api_port}
            onChange={(e) => update("api_port", Number(e.target.value))}
            className="input"
          />
        </Field>
      </div>
      <div className="text-xs text-muted -mt-2">
        Any one node in the cluster -- PyXie reads cluster-wide state from a single node's API and discovers the
        rest (other nodes, VMs, storage) from there.
      </div>
      <label className="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" checked={form.tls_verify} onChange={(e) => update("tls_verify", e.target.checked)} />
        Verify TLS certificate
      </label>
      <div className="text-xs text-muted pt-1">
        Read-only API token for the <code>inventory</code> credential purpose. In Proxmox, create it under Datacenter
        → Permissions → API Tokens on a dedicated user (e.g. <code>pyxie-manager@pve</code>) -- do not use root@pam.
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Token user (user@realm)">
          <input
            required
            value={form.token_user}
            onChange={(e) => update("token_user", e.target.value)}
            className="input"
            placeholder="pyxie-manager@pve"
          />
        </Field>
        <Field label="Token ID">
          <input
            required
            value={form.token_id}
            onChange={(e) => update("token_id", e.target.value)}
            className="input"
            placeholder="inventory"
          />
        </Field>
      </div>
      <Field label="Token secret">
        <input
          required
          type="password"
          value={form.token_secret}
          onChange={(e) => update("token_secret", e.target.value)}
          className="input"
        />
      </Field>
      {error && <div className="text-xs text-bad">{error}</div>}
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={pending}
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save target"}
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs text-muted space-y-1">
      <span>{label}</span>
      {children}
    </label>
  );
}
