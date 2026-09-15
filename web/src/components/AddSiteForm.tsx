"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

export default function AddSiteForm({ organizationId }: { organizationId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", slug: "" });
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/sites", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, organization_id: organizationId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setOpen(false);
      setForm({ name: "", slug: "" });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-text border border-border hover:bg-surface2"
      >
        + Add Site
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="bg-surface2 border border-border rounded-lg p-4 space-y-3 max-w-md">
      <div className="text-sm font-semibold text-text">New Site</div>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-muted space-y-1">
          <span>Name</span>
          <input
            required
            className="input"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="e.g. Branch Office"
          />
        </label>
        <label className="block text-xs text-muted space-y-1">
          <span>Slug</span>
          <input
            required
            className="input"
            value={form.slug}
            onChange={(e) => setForm({ ...form, slug: e.target.value })}
            placeholder="branch-office"
          />
        </label>
      </div>
      {error && <div className="text-xs text-bad">{error}</div>}
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={pending}
          className="px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save site"}
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
          background: #0b0e14;
          border: 1px solid #232a38;
          border-radius: 6px;
          padding: 6px 8px;
          font-size: 0.875rem;
          color: #e6e9ef;
        }
      `}</style>
    </form>
  );
}
