"use client";

import { useEffect, useRef, useState } from "react";
import type { Workload } from "@/lib/api";

function labelFor(w: Workload): string {
  return w.name ? `${w.name} (vmid ${w.vmid})` : `vmid ${w.vmid}`;
}

export default function WorkloadSearchSelect({
  vms,
  value,
  onChange,
}: {
  vms: Workload[];
  value: string;
  onChange: (id: string) => void;
}) {
  const selected = vms.find((w) => w.id === value) || null;
  const [query, setQuery] = useState(selected ? labelFor(selected) : "");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Stay in sync if the selection changes from outside this component (e.g.
  // cleared elsewhere on the page after an operation completes).
  useEffect(() => {
    const w = vms.find((v) => v.id === value) || null;
    setQuery(w ? labelFor(w) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  const q = query.trim().toLowerCase();
  const matches = q ? vms.filter((w) => (w.name || "").toLowerCase().includes(q) || String(w.vmid).includes(q)) : vms;

  function pick(w: Workload) {
    onChange(w.id);
    setQuery(labelFor(w));
    setOpen(false);
  }

  return (
    <div className="relative" ref={containerRef}>
      <input
        className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[220px] w-full"
        placeholder="Search VMs by name or vmid…"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          if (value) onChange("");
        }}
        onFocus={() => setOpen(true)}
      />
      {open && (
        <div className="absolute z-10 mt-1 w-full max-h-64 overflow-y-auto bg-surface2 border border-border rounded shadow-lg">
          {matches.length === 0 && <div className="px-2 py-1.5 text-xs text-muted italic">No matches.</div>}
          {matches.map((w) => (
            <button
              key={w.id}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(w)}
              className={`block w-full text-left px-2 py-1.5 text-sm hover:bg-accent/10 truncate ${
                w.id === value ? "bg-accent/10" : ""
              }`}
            >
              {labelFor(w)} · {w.status}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
