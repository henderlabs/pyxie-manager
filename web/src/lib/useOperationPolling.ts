"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { isTerminal } from "@/lib/operationStatus";
import { isInFlight } from "@/lib/operationStatus";

/** Polls GET /api/operations/{id} every 2s while the operation is in flight,
 * stopping automatically once it reaches a terminal status. Shared by any
 * component that needs to watch one operation to completion -- the migrate
 * form's own freshly-created operation, and the recent-operations list
 * watching one a user approved from history. */
export function useOperationPolling(initial: Operation | null): [Operation | null, (op: Operation | null) => void] {
  const [op, setOp] = useState<Operation | null>(initial);

  useEffect(() => {
    if (!op || isTerminal(op.status)) return;
    // Still waiting on approval: poll slowly so an approval made from the Task
    // Panel (or another tab) shows up here too, not just ones made in this card.
    const interval = setInterval(async () => {
      const res = await fetch(`/api/operations/${op.id}`);
      if (res.ok) setOp(await res.json());
    }, isInFlight(op.status) ? 2000 : 5000);
    return () => clearInterval(interval);
  }, [op]);

  return [op, setOp];
}
