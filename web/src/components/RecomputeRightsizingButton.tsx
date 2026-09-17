"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

/** Rightsizing used to recompute live on every page load -- confirmed as
 * the real cause of multi-second Workloads/Maintenance/this-page loads,
 * even after the underlying queries were batched. It's now served from a
 * cache the worker's regular ~5-minute cycle keeps fresh; this button is
 * the manual "don't wait for the next cycle" escape hatch. */
export default function RecomputeRightsizingButton({ computedAt }: { computedAt: string | null }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) {
    return computedAt ? (
      <span className="text-xs text-muted">Last computed {new Date(computedAt).toLocaleString()}</span>
    ) : null;
  }

  async function recompute() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/rightsizing/recompute", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex items-center gap-2 text-xs">
      {computedAt && <span className="text-muted">Last computed {new Date(computedAt).toLocaleString()}</span>}
      <button
        onClick={recompute}
        disabled={pending}
        className="px-2 py-1 rounded font-medium bg-surface2 text-text border border-border hover:bg-surface2/70 disabled:opacity-50"
      >
        {pending ? "Recomputing…" : "Recompute now"}
      </button>
      {error && <span className="text-bad">{error}</span>}
    </div>
  );
}
