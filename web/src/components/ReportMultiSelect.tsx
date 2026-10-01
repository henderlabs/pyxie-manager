"use client";

import { useMemo, useRef, useState, useEffect } from "react";

export type PickOption = { id: string; label: string; sub?: string };

/** Searchable checkbox dropdown for the Reporting scope pickers
 * (clusters / hosts / VMs). Empty selection means "all" -- the same
 * convention the API uses, so nothing selected is never an empty report. */
export default function ReportMultiSelect({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: PickOption[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () => (q ? options.filter((o) => o.label.toLowerCase().includes(q) || (o.sub || "").toLowerCase().includes(q)) : options),
    [options, q]
  );

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  }

  const summary =
    selected.size === 0
      ? "All"
      : selected.size === 1
        ? options.find((o) => selected.has(o.id))?.label ?? "1 selected"
        : `${selected.size} selected`;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`text-sm px-3 py-1.5 rounded border bg-surface2 hover:bg-surface ${
          selected.size ? "border-accent text-text" : "border-border text-muted"
        }`}
      >
        <span className="text-muted">{label}:</span> <span className="text-text">{summary}</span> ▾
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-72 bg-surface2 border border-border rounded-lg shadow-lg">
          <div className="p-2 border-b border-border flex items-center gap-2">
            <input
              autoFocus
              className="bg-surface border border-border rounded px-2 py-1 text-sm flex-1 min-w-0"
              placeholder={`Search ${label.toLowerCase()}…`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              type="button"
              className="text-xs text-muted hover:text-text disabled:opacity-40"
              disabled={selected.size === 0}
              onClick={() => onChange(new Set())}
            >
              Clear
            </button>
          </div>
          <div className="max-h-64 overflow-y-auto py-1">
            {shown.length === 0 && <div className="px-3 py-2 text-xs text-muted italic">No matches.</div>}
            {shown.map((o) => (
              <label key={o.id} className="flex items-center gap-2 px-3 py-1 text-sm hover:bg-surface cursor-pointer">
                <input type="checkbox" checked={selected.has(o.id)} onChange={() => toggle(o.id)} />
                <span className="text-text truncate">{o.label}</span>
                {o.sub && <span className="text-xs text-muted ml-auto shrink-0">{o.sub}</span>}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
