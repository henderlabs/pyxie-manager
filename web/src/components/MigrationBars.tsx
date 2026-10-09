"use client";

import { formatBytes } from "@/lib/format";
import type { Operation } from "@/lib/api";

type Part = { transferred_bytes: number | null; total_bytes: number | null; rate_bytes_per_sec: number | null; pct: number | null; done?: boolean };

function Bar({ label, part, waiting, tone }: { label: string; part: Part | null | undefined; waiting?: boolean; tone: "storage" | "vm" }) {
  const pct = part?.pct ?? null;
  const done = part?.done || (pct != null && pct >= 100);
  const color = tone === "storage" ? "bg-accent" : "bg-proxmox";
  return (
    <div className="mt-1.5">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="text-muted">{label}</span>
        <span className="text-muted tabular-nums truncate">
          {waiting && !part ? "waiting" : part && part.total_bytes
            ? `${formatBytes(part.transferred_bytes ?? 0)} of ${formatBytes(part.total_bytes)}${pct != null ? ` (${Math.round(pct)}%)` : ""}${part.rate_bytes_per_sec ? ` · ${formatBytes(part.rate_bytes_per_sec)}/s` : ""}${done ? " · done" : ""}`
            : "starting…"}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-border overflow-hidden relative">
        {pct != null ? (
          <div className={`h-full ${done ? "bg-good" : color} transition-all`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
        ) : waiting ? null : (
          <div className={`absolute inset-y-0 w-1/3 ${color} rounded-full animate-[taskpanel-indeterminate_1.3s_ease-in-out_infinite]`} />
        )}
      </div>
    </div>
  );
}

/** What a live migration is doing right now: the VM's memory moving between hosts and, when its disks are on node-local
 *  storage, the disks being copied to the target storage first. Two bars when both happen, one when only the VM moves. */
export default function MigrationBars({ op }: { op: Operation }) {
  const ctx = (op.context || {}) as Record<string, unknown>;
  const target = typeof ctx.target_storage_name === "string" ? ctx.target_storage_name : null;
  const p = op.progress as (Operation["progress"] & { storage?: Part; vm?: Part; phase?: string }) | null;
  const movesStorage = !!p?.storage || !!target;
  const heading = movesStorage ? `Moving the VM and its disks${target ? ` to ${target}` : ""}` : "Moving the VM between hosts";
  return (
    <div className="mt-1.5">
      <div className="text-[11px] text-text">{heading}{p?.phase === "storage" ? " · copying disks" : p?.phase === "vm" ? " · moving memory" : ""}</div>
      {movesStorage && <Bar label={`Disk copy${target ? ` → ${target}` : ""}`} part={p?.storage} tone="storage" />}
      <Bar label={movesStorage ? "VM memory (host to host)" : "VM memory"} part={p?.vm} waiting={movesStorage} tone="vm" />
    </div>
  );
}
