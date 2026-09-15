"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Organization } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function OrganizationForm({ initial }: { initial: Organization }) {
  const router = useRouter();
  const [name, setName] = useState(initial.name);
  const [slug, setSlug] = useState(initial.slug);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      const res = await fetch(`/api/organizations/${initial.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setSaved(true);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 max-w-lg">
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-muted space-y-1">
          <span>Organization name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} readOnly={!isAdmin} required />
        </label>
        <label className="block text-xs text-muted space-y-1">
          <span>Slug</span>
          <input className="input" value={slug} onChange={(e) => setSlug(e.target.value)} readOnly={!isAdmin} required />
        </label>
      </div>
      {error && <div className="text-xs text-bad">{error}</div>}
      {saved && !error && <div className="text-xs text-good">Saved.</div>}
      {isAdmin && (
        <button
          type="submit"
          disabled={pending}
          className="px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save organization"}
        </button>
      )}
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
