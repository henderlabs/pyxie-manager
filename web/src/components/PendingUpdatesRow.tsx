"use client";

import { useState } from "react";

type PvePackage = { Package: string; OldVersion: string; Version: string };

export default function PendingUpdatesRow({ nodeId, initialCount }: { nodeId: string; initialCount: number }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [packages, setPackages] = useState<PvePackage[] | null>(null);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (packages != null) return; // already fetched once this page load
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/pending-updates`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setPackages(data.packages || []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="py-0.5 text-sm">
      <div className="flex justify-between items-center">
        <dt className="text-muted">Pending updates</dt>
        <dd className="text-text">
          {initialCount}{" "}
          {initialCount > 0 && (
            <button onClick={toggle} className="text-accent hover:underline ml-1">
              {open ? "hide" : "view"}
            </button>
          )}
        </dd>
      </div>
      {open && (
        <div className="mt-1.5 border border-border rounded max-h-64 overflow-y-auto">
          {loading && <div className="text-xs text-muted italic px-2 py-1.5">Loading…</div>}
          {error && <div className="text-xs text-bad px-2 py-1.5">{error}</div>}
          {packages &&
            packages.map((p, i) => (
              <div key={i} className="flex justify-between px-2 py-1 text-xs odd:bg-surface/40 font-mono">
                <span className="text-text">{p.Package}</span>
                <span className="text-muted">
                  {p.OldVersion || "(new)"} <span className="text-accent">→</span> {p.Version}
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
