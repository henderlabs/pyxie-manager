"use client";

import { useCallback, useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Job = { id: string; storage: string | null; schedule: string | null; enabled: boolean; mode: string | null; retention: string | null; next_run: number | null; by_policy: boolean; covers: string };
type Plan = {
  policy: { id: string; name: string; retention: string; schedule: string; storage: string; mode: string; source: string; is_default?: boolean; layers?: { label: string; text: string }[] } | null;
  exempt: boolean; source: string | null; jobs: Job[]; error: string | null; policies: { id: string; name: string }[]; cluster_id: string; default_policy_id?: string | null;
};
const input = "bg-surface border border-border rounded px-2 py-1 text-xs";

/** One guest's backup plan: the policy that applies (and why), every layer it makes, and the one dropdown that changes it. */
export default function GuestPlan({ workloadId, name }: { workloadId: string; name: string }) {
  const me = useMe();
  const canEdit = me === undefined || me?.is_admin === true;
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pend, setPend] = useState<Operation | null>(null);
  const load = useCallback(async () => {
    try { const r = await fetch(`/api/backup-policies/workload/${workloadId}`); const d = await r.json(); if (!r.ok) throw new Error(d.error || "Could not read the backup plan"); setPlan(d); }
    catch (e) { setErr((e as Error).message); }
  }, [workloadId]);
  useEffect(() => { load(); }, [load]);

  async function change(v: string) {
    if (!plan) return;
    setBusy(true); setErr(null);
    const change = v === "__inherit" ? { action: "assign", clear: [{ scope_type: "vm", scope_value: workloadId }] }
      : { action: "assign", set: [{ scope_type: "vm", scope_value: workloadId, policy_id: v === "__none" ? null : v }] };
    try {
      const r = await fetch("/api/backup-policies/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: plan.cluster_id, change, leave_other: true }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Could not check that");
      setPend(d); notifyOperationsChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (pend) return <div className="text-xs"><OpPreview key={pend.id} op={pend} confirmLabel="Confirm" progressText="Changing the backup jobs" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} /></div>;
  if (!plan) return <div className="text-xs text-muted">{err ?? "Reading the backup plan…"}</div>;
  const sel = plan.exempt ? "__none" : plan.source === "set for this guest" ? plan.policy?.id ?? "__inherit" : "__inherit";
  const others = plan.jobs.filter((j) => !j.by_policy);
  return (
    <div className="text-xs space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-muted">Backup plan for {name}:</span>
        {plan.policy ? <span className="text-text font-medium">{plan.policy.name}</span> : plan.exempt ? <span className="text-warn">no backup (chosen for this guest)</span> : <span className="text-warn">no policy applies</span>}
        {plan.source && <span className="text-muted">({plan.source})</span>}
        {canEdit && <select className={input} disabled={busy} value={sel} onChange={(e) => change(e.target.value)} aria-label="Policy for this guest">
          <option value="__inherit">Follow the default / tag</option><option value="__none">No backup</option>{plan.policies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>}
      </div>
      {plan.policy && (
        <div className="text-muted pl-3 border-l border-border">
          <div><span className="text-text">Backup</span> to {plan.policy.storage} at {plan.policy.schedule} ({plan.policy.mode}); {plan.policy.retention}.</div>
          {(plan.policy.layers ?? []).map((l, i) => <div key={i}><span className="text-text">{l.label}</span> {l.text}</div>)}
        </div>
      )}
      {others.length > 0 && <div className="text-warn">{plan.policy ? "Also in" : "Backed up by"} the Proxmox job{others.length > 1 ? "s" : ""} {others.map((j) => `${j.id} (${j.schedule ?? "?"} to ${j.storage ?? "?"})`).join(", ")}, which no policy manages.</div>}
      {err && <div className="text-bad">{err}</div>}
      {plan.error && <div className="text-warn">{plan.error}</div>}
    </div>
  );
}
