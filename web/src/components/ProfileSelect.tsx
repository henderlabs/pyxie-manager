"use client";

import { useState } from "react";
import { useMe } from "@/lib/useMe";

/** Throws on a failed save instead of resolving silently -- every select
 * below (and the Notes cell) relies on this to know a PATCH didn't
 * actually take, rather than optimistically assuming success -- a failed
 * save used to look
 * identical to a successful one until the next page refresh. */
export async function patchPlacementProfile(workloadId: string, body: Record<string, unknown>): Promise<void> {
  const res = await fetch(`/api/workloads/${workloadId}/placement-profile`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Save failed (${res.status})`);
  }
}

export function ProfileSelect({
  workloadId,
  field,
  value,
  options,
  highlightWhen,
  onChanged,
}: {
  workloadId: string;
  field: "sensitivity" | "downtime_tolerance";
  value: string;
  options: string[];
  highlightWhen: string;
  onChanged: (v: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  async function change(newValue: string) {
    setSaving(true);
    setError(null);
    try {
      await patchPlacementProfile(workloadId, { [field]: newValue });
      onChanged(newValue);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <select
      className={`bg-surface2 border rounded px-1.5 py-1 text-xs ${error ? "border-bad" : "border-border"} ${value === highlightWhen ? "text-warn" : ""}`}
      value={value}
      disabled={saving || !isAdmin}
      title={error || undefined}
      onChange={(e) => change(e.target.value)}
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

