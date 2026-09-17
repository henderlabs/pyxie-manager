"use client";

import { useState } from "react";
import { LogDetailGrid } from "@/components/LogDetail";

type OperationSummary = {
  id: string;
  operation_type_id: string;
  status: string;
  created_by: string | null;
  approved_by: string | null;
  pve_upid: string | null;
  error: string | null;
};

/** Closes the loop Phil asked about: PVE only ever shows a task as run by
 * whichever credential PyXie used (e.g. pyxie-maint@pve) -- this resolves
 * the actual PyXie Operation behind it, so "who in PyXie asked for this"
 * is one click away instead of two separate tables you'd have to
 * cross-reference by hand. Shared by the PVE Tasks detail panel (task ->
 * operation) and the Audit Log detail panel (operation event -> full
 * operation detail), fetched on demand via the same GET
 * /api/operations/{id} the live operation-polling UI already uses. */
export default function OperationLinkViewer({ operationId }: { operationId: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [op, setOp] = useState<OperationSummary | null>(null);

  async function fetchOp() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${operationId}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setOp(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (op) {
    return (
      <div className="mt-2">
        <div className="text-[10px] text-muted uppercase tracking-wide mb-1">PyXie operation</div>
        <LogDetailGrid
          fields={[
            ["Type", op.operation_type_id],
            ["Status", op.status],
            ["Created by", op.created_by],
            ["Approved by", op.approved_by],
            ["PVE UPID", op.pve_upid],
            ["Error", op.error],
          ]}
        />
      </div>
    );
  }

  return (
    <div className="mt-2">
      <button
        onClick={fetchOp}
        disabled={pending}
        className="px-2 py-1 rounded text-xs font-medium bg-surface2 text-text border border-border hover:bg-surface2/70 disabled:opacity-50"
      >
        {pending ? "Loading…" : "View PyXie operation"}
      </button>
      {error && <div className="text-xs text-bad mt-1">{error}</div>}
    </div>
  );
}
