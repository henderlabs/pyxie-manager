"use client";

import { useState } from "react";
import { useMe } from "@/lib/useMe";

// Extracted from the old standalone Clusters page's inline selector
// (2026-09-12, consolidated into Hosts & Clusters) -- same
// placement.storage_preference policy, same fallback semantics: a per-VM
// preference (Workloads page) always wins over this.
export default function ClusterStoragePreference({
  clusterId,
  initialValue,
}: {
  clusterId: string;
  initialValue: string | null;
}) {
  const [value, setValue] = useState(initialValue);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function change(raw: string) {
    const newValue = raw === "" ? null : raw;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/policies", {
        method: "PUT",
        body: JSON.stringify({
          scope_type: "cluster",
          scope_id: clusterId,
          key: "placement.storage_preference",
          value: newValue,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Save failed (${res.status})`);
      }
      setValue(newValue);
    } catch (e) {
      // Same optimistic-save bug pattern fixed elsewhere in this app,
      // caught in a sweep after fixing the first couple of instances.
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
    <select
      className={`bg-surface2 border rounded px-2 py-1.5 text-sm ${error ? "border-bad" : "border-border"}`}
      value={value ?? ""}
      disabled={saving || !isAdmin}
      title={error || undefined}
      onChange={(e) => change(e.target.value)}
    >
      <option value="">Not set -- infer per VM from current location</option>
      <option value="local">Local -- new/migrated VMs prefer node-local disk</option>
      <option value="shared">Shared -- new/migrated VMs prefer cluster-shared storage</option>
    </select>
    {error && <div className="text-bad text-[10px] mt-0.5">{error}</div>}
    </div>
  );
}
