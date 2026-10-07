"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Chunk = { seq: number; ts: string; source: "host" | "stage"; text: string };

function clock(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Live output for a host update or reboot: the host's own apt output plus the workflow's progress lines.
 * Polls every 2 s while the operation is running; for a finished one it loads once, collapsed. */
export default function OperationLogPanel({ operationId, active }: { operationId: string; active: boolean }) {
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [open, setOpen] = useState(active);
  const [follow, setFollow] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const nextRef = useRef(0);
  const boxRef = useRef<HTMLPreElement>(null);

  const fetchMore = useCallback(async () => {
    try {
      const r = await fetch(`/api/operations/${operationId}/log?after=${nextRef.current}`, { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      setError(null);
      setLoaded(true);
      if (d.lines?.length) {
        nextRef.current = d.next;
        setChunks((prev) => [...prev, ...d.lines]);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [operationId]);

  useEffect(() => { if (active) setOpen(true); }, [active]);

  useEffect(() => {
    if (!open) return;
    fetchMore();
    if (!active) return;
    const t = setInterval(fetchMore, 2000);
    return () => clearInterval(t);
  }, [open, active, fetchMore]);

  // One last fetch when the operation stops being active, to pick up the final lines.
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) fetchMore();
    wasActive.current = active;
  }, [active, fetchMore]);

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [chunks, follow]);

  if (!open) {
    return (
      <div className="mb-2">
        <button type="button" onClick={() => setOpen(true)} className="text-xs text-accent hover:underline">Show log</button>
      </div>
    );
  }

  return (
    <div className="mb-3">
      <div className="flex items-center gap-3 text-[11px] text-muted mb-1">
        <span className="uppercase tracking-wide">Log</span>
        {active && <span className="text-good">● live</span>}
        <label className="ml-auto flex items-center gap-1"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow</label>
        {!active && <button type="button" onClick={() => setOpen(false)} className="hover:text-text">Hide</button>}
      </div>
      <pre ref={boxRef} className="text-xs font-mono bg-canvas/60 border border-border rounded p-2 max-h-64 overflow-y-auto whitespace-pre-wrap break-words">
        {chunks.length === 0
          ? (error ? `Could not load the log (${error}).` : loaded ? (active ? "Waiting for output…" : "No log was recorded for this operation.") : "Loading…")
          : chunks.map((c) =>
              c.source === "stage"
                ? <span key={c.seq} className="text-muted">{`[${clock(c.ts)}] ${c.text}`}</span>
                : <span key={c.seq}>{c.text}</span>
            )}
      </pre>
    </div>
  );
}
