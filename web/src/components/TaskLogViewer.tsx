"use client";

import { useState } from "react";

/** The PVE Tasks list only ever stores PVE's short summary status ("OK" or
 * a one-line error) -- never the full log a real diagnosis needs. This
 * fetches PVE's own append-only task log on demand (not eagerly for every
 * row -- most tasks succeed and nobody needs the log) via
 * GET /api/tasks/{id}/log. */
export default function TaskLogViewer({ taskId }: { taskId: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<{ n: number; t: string }[] | null>(null);

  async function fetchLog() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/tasks/${taskId}/log`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      if (data.error) {
        setError(data.error);
        return;
      }
      setLines(data.lines || []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (lines) {
    return (
      <div className="mt-2">
        <div className="text-[10px] text-muted uppercase tracking-wide mb-1">Full PVE task log</div>
        <pre className="text-xs font-mono bg-canvas/60 border border-border rounded p-2 max-h-64 overflow-y-auto whitespace-pre-wrap">
          {lines.length === 0 ? "(empty)" : lines.map((l) => `${l.t}`).join("\n")}
        </pre>
      </div>
    );
  }

  return (
    <div className="mt-2">
      <button
        onClick={fetchLog}
        disabled={pending}
        className="px-2 py-1 rounded text-xs font-medium bg-surface2 text-text border border-border hover:bg-surface2/70 disabled:opacity-50"
      >
        {pending ? "Fetching…" : "Fetch full PVE task log"}
      </button>
      {error && <div className="text-xs text-bad mt-1">{error}</div>}
    </div>
  );
}
