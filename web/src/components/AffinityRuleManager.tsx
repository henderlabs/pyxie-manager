"use client";

import { useState } from "react";
import type { Workload } from "@/lib/api";
import { Card, CardTitle, EmptyState } from "@/components/Card";
import { useMe } from "@/lib/useMe";
import WorkloadSearchSelect from "@/components/WorkloadSearchSelect";

type Rule = {
  id: string;
  rule_type: string;
  scope_type: string;
  workload_ids: string[] | null;
  tag: string;
  strict: boolean;
  description: string | null;
  created_by: string | null;
};

const EMPTY_FORM = { ruleType: "keep_apart", scopeType: "tag_group", workloadA: "", workloadB: "", tag: "", strict: true, description: "" };

export default function AffinityRuleManager({ initialRules, workloads }: { initialRules: Rule[]; workloads: Workload[] }) {
  const [rules, setRules] = useState(initialRules);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  const workloadById = new Map(workloads.map((w) => [w.id, w]));

  function startEdit(r: Rule) {
    setEditingId(r.id);
    setForm({
      ruleType: r.rule_type,
      scopeType: r.scope_type,
      workloadA: r.workload_ids?.[0] || "",
      workloadB: r.workload_ids?.[1] || "",
      tag: r.tag || "",
      strict: r.strict,
      description: r.description || "",
    });
    setError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setError(null);
  }

  async function submit() {
    setPending(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        rule_type: form.ruleType,
        scope_type: form.scopeType,
        strict: form.strict,
        description: form.description || null,
      };
      if (form.scopeType === "workload_pair") {
        if (!form.workloadA || !form.workloadB || form.workloadA === form.workloadB) {
          setError("Pick two different workloads");
          return;
        }
        body.workload_ids = [form.workloadA, form.workloadB];
      } else {
        if (!form.tag) {
          setError("Tag is required");
          return;
        }
        body.tag = form.tag;
      }

      const url = editingId ? `/api/placement/affinity-rules/${editingId}` : "/api/placement/affinity-rules";
      const res = await fetch(url, { method: editingId ? "PUT" : "POST", body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || (editingId ? "Failed to update rule" : "Failed to create rule"));
        return;
      }
      if (editingId) {
        setRules((rs) => rs.map((r) => (r.id === editingId ? data : r)));
      } else {
        setRules((rs) => [data, ...rs]);
      }
      cancelEdit();
    } finally {
      setPending(false);
    }
  }

  async function deleteRule(id: string) {
    await fetch(`/api/placement/affinity-rules/${id}`, { method: "DELETE" });
    setRules((r) => r.filter((x) => x.id !== id));
    if (editingId === id) cancelEdit();
  }

  return (
    <>
      {isAdmin && (
      <Card className="mb-4">
        <CardTitle>{editingId ? "Edit Rule" : "New Rule"}</CardTitle>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-muted mb-1">Type</label>
            <select
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm"
              value={form.ruleType}
              onChange={(e) => setForm((f) => ({ ...f, ruleType: e.target.value }))}
            >
              <option value="keep_apart">keep apart</option>
              <option value="keep_together">keep together</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Scope</label>
            <select
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm"
              value={form.scopeType}
              onChange={(e) => setForm((f) => ({ ...f, scopeType: e.target.value }))}
            >
              <option value="tag_group">tag group</option>
              <option value="workload_pair">workload pair</option>
            </select>
          </div>
          {form.scopeType === "tag_group" ? (
            <div>
              <label className="block text-xs text-muted mb-1">Tag</label>
              <input
                className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm"
                value={form.tag}
                onChange={(e) => setForm((f) => ({ ...f, tag: e.target.value }))}
                placeholder="e.g. dns"
              />
            </div>
          ) : (
            <>
              <div>
                <label className="block text-xs text-muted mb-1">Workload A</label>
                <WorkloadSearchSelect vms={workloads} value={form.workloadA} onChange={(id) => setForm((f) => ({ ...f, workloadA: id }))} />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Workload B</label>
                <WorkloadSearchSelect vms={workloads} value={form.workloadB} onChange={(id) => setForm((f) => ({ ...f, workloadB: id }))} />
              </div>
            </>
          )}
          <div>
            <label className="block text-xs text-muted mb-1">Description</label>
            <input
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[220px]"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="why this rule exists"
            />
          </div>
          <label className="flex items-center gap-1.5 text-sm text-text pb-1.5">
            <input type="checkbox" checked={form.strict} onChange={(e) => setForm((f) => ({ ...f, strict: e.target.checked }))} />
            Hard block (uncheck for a soft scoring preference only)
          </label>
          <button
            onClick={submit}
            disabled={pending}
            className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            {pending ? "Saving…" : editingId ? "Save Changes" : "Create Rule"}
          </button>
          {editingId && (
            <button onClick={cancelEdit} className="px-3 py-1.5 rounded text-sm font-medium border border-border text-text hover:bg-surface2">
              Cancel
            </button>
          )}
        </div>
        {error && <div className="text-sm text-bad mt-2">{error}</div>}
      </Card>
      )}

      <Card>
        <CardTitle>Active Rules</CardTitle>
        {rules.length === 0 ? (
          <EmptyState message="No affinity rules defined yet." />
        ) : (
          <div className="divide-y divide-border">
            {rules.map((r) => (
              <div key={r.id} className="flex items-center justify-between py-2.5">
                <div>
                  <div className="text-sm text-text">
                    {r.rule_type === "keep_apart" ? "Keep apart" : "Keep together"}
                    {" · "}
                    {r.scope_type === "tag_group"
                      ? `tag: ${r.tag}`
                      : (r.workload_ids || [])
                          .map((id) => {
                            const w = workloadById.get(id);
                            return w ? (w.name ? `${w.name} (vmid ${w.vmid})` : `vmid ${w.vmid}`) : id;
                          })
                          .join(" + ")}
                    {!r.strict && " · soft"}
                  </div>
                  {r.description && <div className="text-xs text-muted">{r.description}</div>}
                </div>
                {isAdmin && (
                <div className="flex items-center gap-3">
                  <button onClick={() => startEdit(r)} className="text-xs text-accent hover:underline">
                    Edit
                  </button>
                  <button onClick={() => deleteRule(r.id)} className="text-xs text-bad hover:underline">
                    Delete
                  </button>
                </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
