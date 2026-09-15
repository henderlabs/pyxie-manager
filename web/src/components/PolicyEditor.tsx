"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { PolicyRow } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { POLICY_META } from "@/lib/policyMeta";

const UNIT_LABEL: Record<string, string> = {
  percent: "%",
  "count-hours": "hours",
  "count-days": "days",
};

export default function PolicyEditor({ policy, storageNameById = {} }: { policy: PolicyRow; storageNameById?: Record<string, string> }) {
  const router = useRouter();
  const meta = POLICY_META[policy.key];
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  // Raw JSON fallback state -- used when there's no known meta for this
  // key, or the stored value doesn't match the shape meta expects (e.g.
  // hand-edited into something weird -- better to fall back to the honest
  // raw editor than crash or silently coerce it).
  const [raw, setRaw] = useState(JSON.stringify(policy.value));
  // Typed-control state, only used when meta applies.
  const initialTyped = meta ? (meta.field ? (policy.value as Record<string, unknown>)?.[meta.field] : policy.value) : undefined;
  const [typedValue, setTypedValue] = useState<unknown>(initialTyped);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const useTypedControl =
    meta &&
    (meta.control !== "boolean" || typeof initialTyped === "boolean") &&
    (meta.control === "boolean" ||
      meta.control === "tier" ||
      meta.control === "text" ||
      meta.control === "storage-ref" ||
      typeof initialTyped === "number");

  async function saveValue(value: unknown) {
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/policies", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope_type: policy.scope_type, scope_id: policy.scope_id, key: policy.key, value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Save failed");
        return;
      }
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  function saveTyped() {
    const value = meta?.field ? { ...(policy.value as Record<string, unknown>), [meta.field]: typedValue } : typedValue;
    saveValue(value);
  }

  function saveRaw() {
    let value: unknown;
    try {
      value = JSON.parse(raw);
    } catch {
      setError("Value must be valid JSON");
      return;
    }
    saveValue(value);
  }

  if (useTypedControl && meta) {
    return (
      <div className="flex flex-col gap-1.5 w-full max-w-md">
        {meta.control === "boolean" ? (
          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={Boolean(typedValue)}
              disabled={!isAdmin}
              onChange={(e) => setTypedValue(e.target.checked)}
            />
            {typedValue ? "On" : "Off"}
          </label>
        ) : meta.control === "tier" ? (
          <select
            value={String(typedValue ?? "standard")}
            disabled={!isAdmin}
            onChange={(e) => setTypedValue(e.target.value)}
            className="bg-canvas border border-border rounded px-2 py-1.5 text-sm text-text w-40"
          >
            <option value="low">low</option>
            <option value="standard">standard</option>
            <option value="high">high</option>
          </select>
        ) : meta.control === "text" ? (
          <input
            value={String(typedValue ?? "")}
            disabled={!isAdmin}
            onChange={(e) => setTypedValue(e.target.value)}
            className="bg-canvas border border-border rounded px-2 py-1.5 text-sm text-text w-full"
          />
        ) : meta.control === "storage-ref" ? (
          <div className="text-sm text-text" title={typedValue ? String(typedValue) : undefined}>
            {typedValue ? storageNameById[String(typedValue)] || `(unknown storage: ${typedValue})` : "— none set —"}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <input
              type="number"
              value={typeof typedValue === "number" ? typedValue : ""}
              disabled={!isAdmin}
              onChange={(e) => setTypedValue(e.target.value === "" ? "" : Number(e.target.value))}
              className="bg-canvas border border-border rounded px-2 py-1.5 text-sm text-text w-24"
            />
            <span className="text-xs text-muted">{UNIT_LABEL[meta.control]}</span>
          </div>
        )}
        {isAdmin && meta.control !== "storage-ref" && (
          <div className="flex items-center gap-2">
            <button
              onClick={saveTyped}
              disabled={pending}
              className="px-2 py-1 rounded text-xs bg-surface2 border border-border text-text hover:bg-surface2/70 disabled:opacity-50 self-start"
            >
              {pending ? "…" : "Save"}
            </button>
            {error && <span className="text-xs text-bad">{error}</span>}
          </div>
        )}
        {!isAdmin && error && <span className="text-xs text-bad">{error}</span>}
      </div>
    );
  }

  // Fallback: raw JSON, for anything without known meta (or whose stored
  // shape doesn't match what meta expects). Full width and a textarea
  // rather than a fixed-width single-line input -- a long JSON object was
  // previously unreadable in a 10rem-wide <input> regardless of content.
  return (
    <div className="flex flex-col gap-1.5 w-full max-w-lg">
      {!meta && <div className="text-[11px] text-muted">Advanced -- no description available for this key yet.</div>}
      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        readOnly={!isAdmin}
        rows={raw.length > 60 ? 3 : 1}
        className="bg-canvas border border-border rounded px-2 py-1.5 text-xs font-mono text-text w-full resize-y"
      />
      {isAdmin && (
        <div className="flex items-center gap-2">
          <button
            onClick={saveRaw}
            disabled={pending}
            className="px-2 py-1 rounded text-xs bg-surface2 border border-border text-text hover:bg-surface2/70 disabled:opacity-50 self-start"
          >
            {pending ? "…" : "Save"}
          </button>
          {error && <span className="text-xs text-bad">{error}</span>}
        </div>
      )}
      {!isAdmin && error && <span className="text-xs text-bad">{error}</span>}
    </div>
  );
}
