"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Site } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function SiteRow({ site }: { site: Site }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(site.name);
  const [slug, setSlug] = useState(site.slug);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function save() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/sites/${site.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug }),
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
      <div className="flex items-end gap-2 py-1.5">
        <label className="block text-xs text-muted space-y-1">
          <span>Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 160 }} />
        </label>
        <label className="block text-xs text-muted space-y-1">
          <span>Slug</span>
          <input className="input" value={slug} onChange={(e) => setSlug(e.target.value)} style={{ width: 140 }} />
        </label>
        <button
          onClick={save}
          disabled={pending}
          className="px-2 py-1 rounded text-xs font-medium bg-ink text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save"}
        </button>
        <button
          onClick={() => {
            setEditing(false);
            setName(site.name);
            setSlug(site.slug);
          }}
          className="px-2 py-1 rounded text-xs font-medium bg-surface text-muted border border-border"
        >
          Cancel
        </button>
        {error && <div className="text-xs text-bad">{error}</div>}
        <style jsx>{`
          .input {
            background: rgb(var(--c-canvas));
            border: 1px solid rgb(var(--c-border));
            border-radius: 6px;
            padding: 4px 6px;
            font-size: 0.8rem;
            color: rgb(var(--c-text));
          }
        `}</style>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between py-1.5 text-sm">
      <div>
        <span className="text-text">{site.name}</span>
        <span className="text-muted text-xs ml-2">{site.slug}</span>
      </div>
      {isAdmin && (
        <button onClick={() => setEditing(true)} className="text-xs text-accent hover:underline">
          Rename
        </button>
      )}
    </div>
  );
}
