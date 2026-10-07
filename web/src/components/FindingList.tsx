"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Finding } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import { useMe } from "@/lib/useMe";

type View = "active" | "acknowledged" | "dismissed" | "resolved";

/** Health findings with Acknowledge / Dismiss (admins only; viewers just read). Acknowledged rows stay listed but muted
 * and are not counted in the attention numbers; dismissed rows live under their own chip. Both end by themselves when
 * the finding resolves and comes back, or gets worse (findings._reconcile). */
export default function FindingList({ findings, view }: { findings: Finding[]; view: View }) {
  const router = useRouter();
  const me = useMe();
  const isAdmin = me?.is_admin === true;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canTriage = isAdmin && view !== "resolved";

  async function act(ids: string[], action: "acknowledge" | "dismiss" | "reopen") {
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/findings/triage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, action }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      setSelected(new Set());
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  const allSelected = findings.length > 0 && findings.every((f) => selected.has(f.id));
  const btn = "px-2 py-1 rounded bg-surface2 border border-border text-xs hover:bg-surface2/70";

  return (
    <div>
      {error && <div className="mb-2 text-xs text-danger">{error}</div>}
      {canTriage && selected.size > 0 && (
        <div className="flex items-center gap-2 mb-2 px-3 py-2 rounded bg-accent/10 border border-accent text-sm">
          <span>{selected.size} selected</span>
          {view === "active" && (
            <>
              <button disabled={pending} className={`${btn} text-text`} onClick={() => act([...selected], "acknowledge")}>Acknowledge</button>
              <button disabled={pending} className={`${btn} text-muted`} onClick={() => act([...selected], "dismiss")}>Dismiss</button>
            </>
          )}
          {view === "acknowledged" && (
            <>
              <button disabled={pending} className={`${btn} text-muted`} onClick={() => act([...selected], "dismiss")}>Dismiss</button>
              <button disabled={pending} className={`${btn} text-text`} onClick={() => act([...selected], "reopen")}>Un-acknowledge</button>
            </>
          )}
          {view === "dismissed" && (
            <button disabled={pending} className={`${btn} text-text`} onClick={() => act([...selected], "reopen")}>Restore</button>
          )}
        </div>
      )}
      {canTriage && findings.length > 1 && (
        <label className="flex items-center gap-2 pb-2 text-xs text-muted">
          <input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(findings.map((f) => f.id)))} />
          Select all
        </label>
      )}
      <div className="divide-y divide-border">
        {findings.map((f) => {
          const muted = f.triage !== "open" || !f.active;
          return (
            <div key={f.id} className={`py-2 text-sm flex items-start gap-3 ${muted ? "opacity-70" : ""}`}>
              {canTriage && <input type="checkbox" className="mt-1" checked={selected.has(f.id)} onChange={() => toggle(f.id)} aria-label={`Select ${f.title}`} />}
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <StatusBadge status={f.active && f.triage === "open" ? f.severity : "unknown"} />
                    <span className="text-text">{f.title}</span>
                    {f.triage === "acknowledged" && f.acknowledged_at && (
                      <span className="text-xs text-muted">acknowledged by {f.acknowledged_by ?? "unknown"}, {new Date(f.acknowledged_at).toLocaleString()}</span>
                    )}
                    {f.triage === "dismissed" && f.dismissed_at && (
                      <span className="text-xs text-muted">dismissed by {f.dismissed_by ?? "unknown"}, {new Date(f.dismissed_at).toLocaleString()}</span>
                    )}
                  </div>
                  <span className="text-xs text-muted uppercase">{f.category}</span>
                </div>
                <div className="text-xs text-muted mt-0.5 ml-[70px]">
                  first seen {new Date(f.first_observed).toLocaleString()} · last seen {new Date(f.last_observed).toLocaleString()}
                  {!f.active && f.resolved_at && <> · resolved {new Date(f.resolved_at).toLocaleString()}</>}
                  {f.triage === "dismissed" && <> · comes back as a new alert only if it clears and happens again, or gets worse</>}
                </div>
              </div>
              {canTriage && (
                <div className="flex gap-1.5">
                  {view === "active" && (
                    <>
                      <button disabled={pending} className={`${btn} text-text`} onClick={() => act([f.id], "acknowledge")}>Acknowledge</button>
                      <button disabled={pending} className={`${btn} text-muted`} onClick={() => act([f.id], "dismiss")}>Dismiss</button>
                    </>
                  )}
                  {view === "acknowledged" && (
                    <>
                      <button disabled={pending} className={`${btn} text-muted`} onClick={() => act([f.id], "dismiss")}>Dismiss</button>
                      <button disabled={pending} className={`${btn} text-text`} onClick={() => act([f.id], "reopen")}>Un-acknowledge</button>
                    </>
                  )}
                  {view === "dismissed" && (
                    <button disabled={pending} className={`${btn} text-text`} onClick={() => act([f.id], "reopen")}>Restore</button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
