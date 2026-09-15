"use client";

import { useState } from "react";
import { useMe } from "@/lib/useMe";

/** A free-text note, saved on blur (not per-keystroke). Used for the
 * PyXie-only "why did you set this tier/profile" annotation on both Nodes
 * and Workloads -- never synced from/to PVE. Every save is also logged to
 * the Audit Log by the endpoint it calls, so multiple admins get a paper
 * trail of who changed a note and what it said before/after. */
export default function NotesCell({
  value,
  onSave,
}: {
  // onSave must THROW (not just resolve) on a failed save -- this
  // component relies on that to know the save didn't actually happen.
  // The previous contract had no way to signal failure, so a failed save
  // looked identical to a successful one until the next page refresh.
  value: string;
  onSave: (value: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function commit() {
    if (draft === value) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(draft);
    } catch (e) {
      setError((e as Error).message || "Save failed");
      setDraft(value); // don't leave an unsaved edit looking committed
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="w-full min-w-[220px]">
      <input
        type="text"
        className={`bg-surface2 border rounded px-1.5 py-1 text-xs w-full ${error ? "border-bad" : "border-border"}`}
        placeholder="why (PyXie-only, not from PVE)…"
        value={draft}
        disabled={saving}
        readOnly={!isAdmin}
        title={error || undefined}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
      {error && <div className="text-bad text-[10px] mt-0.5">{error}</div>}
    </div>
  );
}
