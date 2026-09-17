"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";

export type Column<T> = {
  header: string;
  render: (row: T) => React.ReactNode;
  sortValue?: (row: T) => string | number | null | undefined;
  /** Overrides the default `<`/`>` comparison on sortValue -- e.g. for
   * version strings like "9.2.18" vs "9.2.3", plain string comparison sorts
   * wrong (see lib/version.ts). Receives the two full rows, not sortValue's
   * output, so it can compare on whatever fields it needs. */
  compare?: (a: T, b: T) => number;
  optional?: boolean;
  className?: string;
  /** Shown as a hover tooltip on a small (i) next to the header -- for
   * columns whose meaning isn't obvious to a new user (e.g. what
   * 'restricted' or 'low' downtime tolerance actually does). */
  tooltip?: string;
};

export type SortState = { header: string; dir: "asc" | "desc" } | null;

/** Extracted from Table's own internal sort so a parent that needs to
 * sort BEFORE slicing (e.g. WorkloadsTable paginating -- sorting must
 * rank across the full dataset, not just whatever page happens to be
 * sliced out) can apply the identical logic instead of duplicating it. */
export function sortRows<T>(rows: T[], columns: Column<T>[], sort: SortState): T[] {
  if (!sort) return rows;
  const col = columns.find((c) => c.header === sort.header);
  if (!col?.sortValue && !col?.compare) return rows;
  let sorted: T[];
  if (col.compare) {
    sorted = [...rows].sort(col.compare);
  } else {
    const withKeys = rows.map((r) => ({ r, v: col.sortValue!(r) }));
    withKeys.sort((a, b) => {
      if (a.v == null && b.v == null) return 0;
      if (a.v == null) return 1;
      if (b.v == null) return -1;
      if (a.v < b.v) return -1;
      if (a.v > b.v) return 1;
      return 0;
    });
    sorted = withKeys.map((w) => w.r);
  }
  if (sort.dir === "desc") sorted.reverse();
  return sorted;
}

type StoredPrefs = {
  order?: string[];
  hidden?: string[];
  sort?: SortState;
  widths?: Record<string, number>;
};

const MIN_COLUMN_WIDTH = 60;

function loadPrefs(storageKey: string): StoredPrefs {
  try {
    const raw = window.localStorage.getItem(`pyxie:table:${storageKey}`);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function savePrefs(storageKey: string, prefs: StoredPrefs) {
  try {
    window.localStorage.setItem(`pyxie:table:${storageKey}`, JSON.stringify(prefs));
  } catch {
    // best-effort only -- a private window or full storage shouldn't break the table
  }
}

export function Table<T extends { id: string }>({
  columns,
  rows,
  emptyMessage = "No data yet.",
  storageKey,
  rowClassName,
  onRowClick,
  renderDetail,
  controlledSort,
  onSortChange,
}: {
  columns: Column<T>[];
  rows: T[];
  emptyMessage?: React.ReactNode;
  storageKey: string;
  /** Extra classes for a specific row -- e.g. highlighting the currently
   * selected one. Appended to the row's default border/hover classes, not
   * a replacement. */
  rowClassName?: (row: T) => string;
  onRowClick?: (row: T) => void;
  /** When provided, every row gets a chevron toggle (independent of
   * onRowClick) that expands a full-width detail panel below it --
   * everything the main columns don't have room for (full envelope, raw
   * IDs, error text), Event Viewer's General/Details-tab style. Return
   * null/undefined for a specific row to skip the toggle for that row
   * only (e.g. nothing more to show than the columns already have). */
  renderDetail?: (row: T) => React.ReactNode;
  /** Together, these hand sorting to the parent instead of Table managing
   * it internally -- e.g. WorkloadsTable sorts its full dataset and THEN
   * paginates, so `rows` here is already sorted (and already sliced to
   * one page); Table just reflects `controlledSort` in the header arrows
   * and calls onSortChange instead of touching its own state. Column
   * order/hidden/width prefs stay internally managed either way. Omit
   * both (the default) for Table's normal self-contained sorting. */
  controlledSort?: SortState;
  onSortChange?: (next: SortState) => void;
}) {
  const [order, setOrder] = useState<string[]>(columns.map((c) => c.header));
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [internalSort, setInternalSort] = useState<SortState>(null);
  const sort = onSortChange ? controlledSort ?? null : internalSort;
  const [widths, setWidths] = useState<Record<string, number>>({});
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const resizingHeader = useRef<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Load persisted per-viewer prefs once on mount; reconcile against the
  // columns actually passed in (a column added/removed since last visit
  // shouldn't break the table).
  useEffect(() => {
    const prefs = loadPrefs(storageKey);
    const validHeaders = new Set(columns.map((c) => c.header));
    if (prefs.order) {
      const kept = prefs.order.filter((h) => validHeaders.has(h));
      const missing = columns.map((c) => c.header).filter((h) => !kept.includes(h));
      setOrder([...kept, ...missing]);
    }
    if (prefs.hidden) {
      setHidden(new Set(prefs.hidden.filter((h) => validHeaders.has(h))));
    }
    if (prefs.sort && validHeaders.has(prefs.sort.header) && !onSortChange) {
      setInternalSort(prefs.sort);
    }
    if (prefs.widths) {
      const kept: Record<string, number> = {};
      for (const [h, w] of Object.entries(prefs.widths)) {
        if (validHeaders.has(h)) kept[h] = w;
      }
      setWidths(kept);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  function persist(next: Partial<StoredPrefs>) {
    const prefs = { order, hidden: Array.from(hidden), sort: internalSort, widths, ...next };
    savePrefs(storageKey, prefs);
  }

  // Manual (non-HTML5-drag) resize -- deliberately a separate mechanism
  // from the header's own native drag-to-reorder below, so dragging the
  // resize handle never gets mistaken for a reorder. Columns need to be
  // both resizable and moveable, on every table.
  // Reads the header cell's OWN current rendered width as the starting
  // point rather than requiring one to already be recorded, so the very
  // first resize of an until-then-auto-sized column still works.
  function startResize(e: React.MouseEvent, header: string, currentPx: number) {
    e.preventDefault();
    e.stopPropagation();
    resizingHeader.current = header;
    const startX = e.clientX;
    function onMove(ev: MouseEvent) {
      const next = Math.max(MIN_COLUMN_WIDTH, currentPx + (ev.clientX - startX));
      setWidths((w) => ({ ...w, [header]: next }));
    }
    function onUp() {
      resizingHeader.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      setWidths((w) => {
        persist({ widths: w });
        return w;
      });
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  const orderedColumns = useMemo(() => {
    const byHeader = new Map(columns.map((c) => [c.header, c]));
    const ordered = order.map((h) => byHeader.get(h)).filter((c): c is Column<T> => !!c);
    // any column not yet in `order` (e.g. first render before the effect runs)
    for (const c of columns) {
      if (!ordered.includes(c)) ordered.push(c);
    }
    return ordered.filter((c) => !hidden.has(c.header));
  }, [columns, order, hidden]);

  const sortedRows = useMemo(() => {
    // Controlled mode: the parent already sorted (and likely paginated)
    // `rows` itself -- sorting again here would just be redundant.
    if (onSortChange) return rows;
    return sortRows(rows, columns, sort);
  }, [rows, sort, columns, onSortChange]);

  function toggleSort(header: string) {
    const col = columns.find((c) => c.header === header);
    if (!col?.sortValue && !col?.compare) return;
    const next: SortState =
      sort?.header === header ? (sort.dir === "asc" ? { header, dir: "desc" } : null) : { header, dir: "asc" };
    if (onSortChange) {
      onSortChange(next);
    } else {
      setInternalSort(next);
      persist({ sort: next });
    }
  }

  function onDrop(targetHeader: string) {
    if (!dragging || dragging === targetHeader) return;
    setOrder((prev) => {
      const withoutDragged = prev.filter((h) => h !== dragging);
      const targetIdx = withoutDragged.indexOf(targetHeader);
      const next = [...withoutDragged.slice(0, targetIdx), dragging, ...withoutDragged.slice(targetIdx)];
      persist({ order: next });
      return next;
    });
    setDragging(null);
  }

  function toggleHidden(header: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(header)) next.delete(header);
      else next.add(header);
      persist({ hidden: Array.from(next) });
      return next;
    });
  }

  const optionalColumns = columns.filter((c) => c.optional);

  return (
    <div>
      {optionalColumns.length > 0 && (
        <div className="flex justify-end mb-2 relative">
          <button
            onClick={() => setPickerOpen((o) => !o)}
            className="text-xs px-2 py-1 rounded border border-border text-muted hover:text-text hover:bg-surface2"
          >
            Columns ▾
          </button>
          {pickerOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setPickerOpen(false)} />
              <div className="absolute right-0 top-7 z-20 bg-surface2 border border-border rounded-lg p-2 text-xs shadow-lg min-w-[160px]">
                {optionalColumns.map((c) => (
                  <label key={c.header} className="flex items-center gap-2 px-2 py-1 rounded hover:bg-surface cursor-pointer">
                    <input type="checkbox" checked={!hidden.has(c.header)} onChange={() => toggleHidden(c.header)} />
                    <span className="text-text">{c.header}</span>
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="text-sm text-muted text-center py-10 border border-dashed border-border rounded-lg">{emptyMessage}</div>
      ) : (
        <div className="overflow-x-auto border border-border rounded-lg">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface2 text-left text-muted text-xs uppercase tracking-wider select-none">
                {renderDetail && <th className="w-8" />}
                {orderedColumns.map((c) => {
                  const isSorted = sort?.header === c.header;
                  const sortable = !!(c.sortValue || c.compare);
                  return (
                    <th
                      key={c.header}
                      draggable
                      onDragStart={() => setDragging(c.header)}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => onDrop(c.header)}
                      style={widths[c.header] ? { width: `${widths[c.header]}px` } : undefined}
                      className={`relative px-3 py-2 font-medium ${c.className || ""} ${dragging === c.header ? "opacity-40" : ""}`}
                    >
                      <span className="inline-flex items-center gap-1.5">
                        <span className="text-muted/60 cursor-grab" title="Drag to reorder">⠿</span>
                        <button
                          type="button"
                          onClick={() => toggleSort(c.header)}
                          disabled={!sortable}
                          title={sortable ? "Click to sort" : undefined}
                          className={`inline-flex items-center gap-1 ${
                            sortable ? "hover:text-text hover:underline cursor-pointer" : "cursor-default"
                          } ${isSorted ? "text-text" : ""}`}
                        >
                          {c.header}
                          {sortable && (
                            <span className={isSorted ? "text-accent" : "text-muted/50"}>
                              {isSorted ? (sort!.dir === "asc" ? "▲" : "▼") : "⇅"}
                            </span>
                          )}
                        </button>
                        {c.tooltip && (
                          <span
                            className="text-muted/70 cursor-help normal-case font-normal"
                            title={c.tooltip}
                          >
                            ⓘ
                          </span>
                        )}
                      </span>
                      <span
                        draggable={false}
                        onDragStart={(e) => e.preventDefault()}
                        onMouseDown={(e) => {
                          const th = e.currentTarget.parentElement as HTMLElement;
                          startResize(e, c.header, th.getBoundingClientRect().width);
                        }}
                        title="Drag to resize"
                        className="absolute top-0 right-0 h-full w-2 cursor-col-resize hover:bg-accent/40 active:bg-accent/60"
                      />
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => {
                const detail = renderDetail?.(row);
                const isExpanded = expanded.has(row.id);
                return (
                  <Fragment key={row.id}>
                    <tr
                      onClick={onRowClick ? () => onRowClick(row) : undefined}
                      className={`border-b border-border last:border-0 hover:bg-surface2/60 ${
                        onRowClick ? "cursor-pointer" : ""
                      } ${rowClassName ? rowClassName(row) : ""}`}
                    >
                      {renderDetail && (
                        <td className="px-1.5 py-2 text-center">
                          {detail != null && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleExpanded(row.id);
                              }}
                              title={isExpanded ? "Hide details" : "Show details"}
                              className="text-muted hover:text-text w-4"
                            >
                              {isExpanded ? "▾" : "▸"}
                            </button>
                          )}
                        </td>
                      )}
                      {orderedColumns.map((c) => (
                        <td
                          key={c.header}
                          className={`px-3 py-2 text-text ${c.className || ""}`}
                        >
                          {c.render(row)}
                        </td>
                      ))}
                    </tr>
                    {isExpanded && detail != null && (
                      <tr className="border-b border-border last:border-0 bg-canvas/60">
                        <td colSpan={orderedColumns.length + 1} className="px-6 py-3">
                          {detail}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
