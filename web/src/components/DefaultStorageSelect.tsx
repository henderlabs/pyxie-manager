"use client";

import { useState } from "react";
import { useMe } from "@/lib/useMe";

// Shared between the Nodes page and the Maintenance page's Nodes panel --
// both write the same node-scoped Policy row, so picking
// it in one place is reflected in the other on next load. 'auto' (stored
// as null) keeps today's behavior: whichever of this node's own local
// storage pools has the most free space at the time of the move.
//
// options includes both this node's own local pools AND the cluster's
// shared storage -- not local-only. Some deployments run local-SSD-first;
// others run shared-first with local barely used, if at all. Picking the
// shared one here pins this node to it outright, same standing-default
// weight as pinning a specific local pool.
export default function DefaultStorageSelect({
  nodeId,
  value,
  autoResolvesTo,
  options,
  onChanged,
}: {
  nodeId: string;
  value: string | null;
  autoResolvesTo?: string;
  options: { id: string; name: string; scope: string }[];
  onChanged: (value: string | null) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function change(newValue: string) {
    const stored = newValue === "auto" ? null : newValue;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/policies", {
        method: "PUT",
        body: JSON.stringify({ scope_type: "node", scope_id: nodeId, key: "placement.default_storage_id", value: stored }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Save failed (${res.status})`);
      }
      onChanged(stored);
    } catch (e) {
      // The previous
      // version ignored res.ok entirely and always called onChanged,
      // so a failed save looked identical to a successful one.
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <select
        className={`bg-surface2 border rounded px-1.5 py-1 text-xs ${error ? "border-bad" : "border-border"}`}
        value={value || "auto"}
        disabled={saving || options.length === 0 || !isAdmin}
        title={error || undefined}
        onChange={(e) => change(e.target.value)}
      >
        <option value="auto">auto (most free space)</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.scope === "cluster-shared" ? `${o.name} (shared)` : o.name}
          </option>
        ))}
      </select>
      {!value && autoResolvesTo && <div className="text-[10px] text-muted mt-0.5">currently: {autoResolvesTo}</div>}
      {options.length === 0 && <div className="text-[10px] text-muted mt-0.5">no local storage on this node</div>}
      {error && <div className="text-bad text-[10px] mt-0.5">{error}</div>}
    </div>
  );
}
