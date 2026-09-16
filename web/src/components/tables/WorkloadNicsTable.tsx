"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import type { WorkloadNic, WorkloadNics } from "@/lib/api";
import { Table } from "@/components/Table";
import { NoInfrastructureHint } from "@/components/Card";
import { useMe } from "@/lib/useMe";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";

type Row = {
  id: string;
  workload_id: string;
  vmid: number;
  name: string | null;
  type: string;
  node: string;
  cluster_name: string;
  nic: WorkloadNic;
};

export default function WorkloadNicsTable({ workloads }: { workloads: WorkloadNics[] }) {
  const rows: Row[] = useMemo(
    () =>
      workloads.flatMap((w) =>
        w.nics.map((nic) => ({
          id: `${w.workload_id}:${nic.net_id}`,
          workload_id: w.workload_id,
          vmid: w.vmid,
          name: w.name,
          type: w.type,
          node: w.node,
          cluster_name: w.cluster_name,
          nic,
        }))
      ),
    [workloads]
  );

  return (
    <Table
      rows={rows}
      emptyMessage={<NoInfrastructureHint subject="workload NICs" />}
      storageKey="infrastructure-network-nics"
      columns={[
        { header: "Workload", render: (r) => r.name || `vmid ${r.vmid}`, sortValue: (r) => r.name || String(r.vmid) },
        { header: "Node", render: (r) => r.node, sortValue: (r) => r.node, optional: true },
        { header: "NIC", render: (r) => r.nic.net_id, sortValue: (r) => r.nic.net_id, optional: true },
        { header: "Bridge", render: (r) => r.nic.bridge || "—", sortValue: (r) => r.nic.bridge },
        {
          header: "VLAN",
          render: (r) => <NicVlanEditor row={r} />,
          sortValue: (r) => r.nic.vlan_tag ?? -1,
        },
      ]}
    />
  );
}

type DryRunOperation = {
  id: string;
  status: string;
  dry_run_result?: {
    current_tag: number | null;
    new_tag: number | null;
    reasons?: string[];
  };
};

function NicVlanEditor({ row }: { row: Row }) {
  const router = useRouter();
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const { nic } = row;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(nic.vlan_tag != null ? String(nic.vlan_tag) : "");
  const [dryRun, setDryRun] = useState<DryRunOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!nic.bridge_vlan_aware) {
    return (
      <span className="text-xs text-muted" title={`Bridge ${nic.bridge || "?"} is not VLAN-aware -- it can't carry tagged traffic.`}>
        {nic.vlan_tag != null ? `VLAN ${nic.vlan_tag}` : "untagged"} <span className="text-muted/60">(fixed)</span>
      </span>
    );
  }

  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-text">{nic.vlan_tag != null ? `VLAN ${nic.vlan_tag}` : "untagged"}</span>
        {isAdmin && (
          <button onClick={() => setEditing(true)} className="text-[11px] text-accent hover:underline">
            change
          </button>
        )}
        {nic.allowed_vlans && <span className="text-[10px] text-muted/60">allowed: {nic.allowed_vlans}</span>}
      </div>
    );
  }

  async function previewChange() {
    setBusy(true);
    setError(null);
    try {
      const newTag = draft.trim() === "" ? null : Number(draft);
      if (newTag !== null && (!Number.isInteger(newTag) || newTag < 1 || newTag > 4094)) {
        setError("VLAN must be 1-4094, or blank for untagged");
        return;
      }
      const res = await fetch("/api/network/vlan-change/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workload_id: row.workload_id, net_id: nic.net_id, new_tag: newTag }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Preview failed");
        return;
      }
      setDryRun(data);
      notifyOperationsChanged();
    } finally {
      setBusy(false);
    }
  }

  async function cancelPreview() {
    if (dryRun) {
      fetch(`/api/operations/${dryRun.id}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismissed: true }),
      }).catch(() => {});
    }
    setDryRun(null);
    setEditing(false);
    setDraft(nic.vlan_tag != null ? String(nic.vlan_tag) : "");
    notifyOperationsChanged();
  }

  async function confirm() {
    if (!dryRun) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${dryRun.id}/approve`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.error || `Approval failed (${res.status})`);
        return;
      }
      notifyOperationsChanged();
      let op: { status: string; error?: string | null; blocking_safety_rules?: string[] } = await res.json();
      while (isInFlight(op.status)) {
        await new Promise((r) => setTimeout(r, 1500));
        const opRes = await fetch(`/api/operations/${dryRun.id}`);
        if (!opRes.ok) break;
        op = await opRes.json();
      }
      notifyOperationsChanged();
      if (op.status === "failed" || op.status === "blocked" || op.status === "cancelled") {
        setDryRun(null);
        setError(op.error || (op.blocking_safety_rules?.length ? op.blocking_safety_rules.join(", ") : `Operation ${op.status}`));
        return;
      }
      setDryRun(null);
      setEditing(false);
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5 min-w-[180px]">
      {!dryRun ? (
        <div className="flex items-center gap-1.5">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="blank = untagged"
            className="bg-canvas border border-border rounded px-1.5 py-1 text-xs text-text w-24"
          />
          <button disabled={busy} onClick={previewChange} className="text-[11px] px-1.5 py-1 rounded bg-surface2 border border-border text-text hover:bg-surface2/70 disabled:opacity-50">
            {busy ? "…" : "Preview"}
          </button>
          <button onClick={cancelPreview} className="text-[11px] text-muted hover:text-text">
            cancel
          </button>
        </div>
      ) : dryRun.status === "blocked" ? (
        <div className="text-xs">
          <div className="text-bad mb-1">{(dryRun.dry_run_result?.reasons || []).join("; ") || "blocked"}</div>
          <button onClick={cancelPreview} className="text-[11px] text-muted hover:text-text">
            back
          </button>
        </div>
      ) : (
        <div className="text-xs">
          <div className="text-text mb-1">
            VLAN {dryRun.dry_run_result?.current_tag ?? "untagged"} → {dryRun.dry_run_result?.new_tag ?? "untagged"}
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={busy}
              onClick={confirm}
              className="px-2 py-1 rounded text-[11px] bg-black text-white border border-warn hover:bg-warn/10 disabled:opacity-50"
            >
              {busy ? "Applying…" : "Confirm & Apply"}
            </button>
            <button onClick={cancelPreview} className="text-muted hover:text-text">
              cancel
            </button>
          </div>
        </div>
      )}
      {error && <div className="text-[11px] text-bad">{error}</div>}
    </div>
  );
}
