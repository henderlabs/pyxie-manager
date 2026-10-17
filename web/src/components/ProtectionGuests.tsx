"use client";

import { useState } from "react";
import Link from "next/link";
import { Table } from "@/components/Table";
import RestorePoints from "@/components/RestorePoints";
import GuestPlan from "@/components/GuestPlan";
import type { BackupJob } from "@/lib/api";

type Copy = { store: string; offsite: boolean; last: number | null; age_text: string; fresh: boolean };
export type GuestRow = { id?: string; workload_id: string; vmid: number; name: string; type: string; status: "ok" | "partial" | "none"; copies: Copy[]; problems: string[]; covered_by: string[] };

const statusText = { ok: "3-2-1 met", partial: "Partly protected", none: "Not protected" } as const;
const statusColor = { ok: "text-good", partial: "text-warn", none: "text-bad" } as const;
const order = { none: 0, partial: 1, ok: 2 } as const;

/** Every guest with its 3-2-1 result; open a row for all of its restore points and to restore from one. */
export default function ProtectionGuests({ rows, stores, jobs }: { rows: GuestRow[]; stores: string[]; jobs: BackupJob[] }) {
  const [q, setQ] = useState("");
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = terms.length ? rows.filter((r) => { const hay = [r.name, r.vmid, r.type === "lxc" ? "ct container" : "vm", statusText[r.status], r.covered_by.join(" "), r.problems.join(" ")].join(" ").toLowerCase(); return terms.every((t) => hay.includes(t)); }) : rows;
  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search guests by name, ID, result, job or what is missing" aria-label="Search guests" className="w-full max-w-md px-3 py-1.5 rounded bg-surface2 border border-border text-sm text-text placeholder:text-muted" />
        {terms.length > 0 && <span className="text-xs text-muted whitespace-nowrap">{shown.length} of {rows.length} guests</span>}
      </div>
    <Table<GuestRow & { id: string }>
      rows={shown.map((r) => ({ ...r, id: r.workload_id }))}
      emptyMessage={terms.length ? "No guest matches the search." : "No guests."}
      storageKey="protection-guests"
      singleExpand
      renderDetail={(r) => <div><div className="px-3 pt-3"><GuestPlan workloadId={r.workload_id} name={r.name} /></div><RestorePoints workloadId={r.workload_id} name={r.name} vmid={r.vmid} /></div>}
      columns={[
        {
          header: "Guest",
          render: (r) => (
            <span className="flex items-center gap-1.5">
              <Link href={`/infrastructure/workloads/${r.workload_id}`} className="text-accent hover:underline">{r.name}</Link>
              <span className="text-xs text-muted">{r.vmid}</span>
              <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-surface2 text-muted">{r.type === "lxc" ? "CT" : "VM"}</span>
            </span>
          ),
          sortValue: (r) => r.name,
        },
        { header: "Result", render: (r) => <span className={statusColor[r.status]}>{statusText[r.status]}</span>, sortValue: (r) => order[r.status] },
        ...stores.map((s) => ({
          header: s,
          render: (r: GuestRow & { id: string }) => {
            const c = r.copies.find((x) => x.store === s);
            return <span className={c?.fresh ? "text-text" : c?.last ? "text-warn" : "text-muted"}>{c?.age_text ?? "never"}</span>;
          },
          sortValue: (r: GuestRow & { id: string }) => r.copies.find((x) => x.store === s)?.last ?? 0,
        })),
        { header: "Backed up by", render: (r) => (r.covered_by.length ? <span className="text-xs">{r.covered_by.join(", ")}</span> : <span className="text-warn text-xs">none</span>), sortValue: (r) => r.covered_by.join(",") },
        { header: "What is missing", render: (r) => (r.problems.length ? <span className="text-xs text-warn block space-y-0.5">{r.problems.map((x, i) => <span key={i} className="block">{x}</span>)}</span> : <span className="text-xs text-muted">Nothing</span>) },
      ]}
    />
    </div>
  );
}
