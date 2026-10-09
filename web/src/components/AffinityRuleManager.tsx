"use client";

import { useRef, useState } from "react";
import type { Node, Workload } from "@/lib/api";
import { Card, CardTitle, EmptyState } from "@/components/Card";
import { useMe } from "@/lib/useMe";
import WorkloadSearchSelect from "@/components/WorkloadSearchSelect";
import AffinityPlacement, { GuestPill, PALETTE, ruleColor, ruleStatus, ruleTail } from "@/components/AffinityPlacement";

type Rule = {
  id: string;
  rule_type: string;
  scope_type: string;
  workload_ids: string[] | null;
  tag: string;
  strict: boolean;
  description: string | null;
  color?: string | null;
  created_by: string | null;
};

const EMPTY_FORM = { ruleType: "keep_apart", scopeType: "tag_group", workloadA: "", workloadB: "", tag: "", strict: true, description: "", color: "" };

export default function AffinityRuleManager({ initialRules, workloads, nodes }: { initialRules: Rule[]; workloads: Workload[]; nodes: Node[] }) {
  const [rules, setRules] = useState(initialRules);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const formRef = useRef<HTMLDivElement>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  const workloadById = new Map(workloads.map((w) => [w.id, w]));
  const tagCounts = new Map<string, number>();
  for (const w of workloads) if (!(w as Workload & { is_missing?: boolean }).is_missing) for (const t of w.tags || []) tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
  const knownTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const typedTag = form.tag.trim();
  const tagMatches = typedTag ? workloads.filter((w) => !(w as Workload & { is_missing?: boolean }).is_missing && (w.tags || []).includes(typedTag)) : [];
  const nodeNameOf = (id: string) => nodes.find((n) => n.id === id)?.name || "unknown node";

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
      color: r.color || "",
    });
    setError(null);
    setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 0);
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
        color: form.color || null,
      };
      if (form.scopeType === "workload_pair") {
        if (!form.workloadA || !form.workloadB || form.workloadA === form.workloadB) {
          setError("Pick two different workloads");
          return;
        }
        body.workload_ids = [form.workloadA, form.workloadB];
      } else {
        if (!typedTag) {
          setError("Tag is required");
          return;
        }
        body.tag = typedTag;
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
    setConfirmDeleteId(null);
    const res = await fetch(`/api/placement/affinity-rules/${id}`, { method: "DELETE" });
    if (!res.ok) {
      setError("Could not delete the rule. Nothing was changed.");
      return;
    }
    setRules((r) => r.filter((x) => x.id !== id));
    if (editingId === id) cancelEdit();
  }

  return (
    <>
      <p className="text-sm text-muted mb-4 max-w-3xl">
        A <span className="text-text">keep apart</span> rule stops guests from sharing a node (for example two domain controllers, so one host failing can&apos;t take both). A{" "}
        <span className="text-text">keep together</span> rule keeps guests on one node (for example an app and its cache). Below: where every guest sits today, then the rules, each with its current status. Click a swatch (or pick any color) when creating or editing a rule to change the color of its line and guest pills.
      </p>
      <AffinityPlacement rules={rules} workloads={workloads} nodes={nodes} />
      {isAdmin && (
      <div ref={formRef}>
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
                list="affinity-known-tags"
              />
              <datalist id="affinity-known-tags">
                {knownTags.map(([t, c]) => (
                  <option key={t} value={t} label={`${c} guest${c === 1 ? "" : "s"}`} />
                ))}
              </datalist>
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
          <div>
            <label className="block text-xs text-muted mb-1">Color (rule line and guest pills)</label>
            <div className="flex items-center gap-1.5">
              {PALETTE.map((c) => (
                <button key={c} type="button" title={c} aria-label={`Use ${c}`} onClick={() => setForm((f) => ({ ...f, color: c }))}
                  className={`w-5 h-5 rounded-full border ${form.color === c ? "border-text ring-2 ring-accent" : "border-border"}`} style={{ background: c }} />
              ))}
              <input type="color" aria-label="Pick any color" title="Pick any color" value={form.color || "#3b82f6"}
                onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} className="w-7 h-6 p-0 bg-transparent border border-border rounded cursor-pointer" />
              {form.color && <button type="button" onClick={() => setForm((f) => ({ ...f, color: "" }))} className="text-xs text-muted hover:underline">Default</button>}
            </div>
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
        {form.scopeType === "tag_group" && (
          <p className="text-xs text-muted mt-2">
            {typedTag === ""
              ? knownTags.length > 0
                ? `Tags in use: ${knownTags.map(([t, c]) => `${t} (${c})`).join(", ")}. Start typing to pick one.`
                : "No guest carries a tag yet. Tags are set on the guest in Proxmox."
              : tagMatches.length === 0
              ? <span className="text-warn">No guest carries the tag &ldquo;{typedTag}&rdquo; (tags are case-sensitive), so this rule would do nothing yet.</span>
              : `Covers ${tagMatches.length} guest${tagMatches.length === 1 ? "" : "s"} right now: ${tagMatches.map((w) => `${w.name || "vmid " + w.vmid} on ${nodeNameOf(w.node_id)}`).join(", ")}.`}
          </p>
        )}
        {error && <div className="text-sm text-bad mt-2">{error}</div>}
      </Card>
      </div>
      )}

      <Card>
        <CardTitle>Active Rules</CardTitle>
        {rules.length === 0 ? (
          <EmptyState message="No affinity rules defined yet." />
        ) : (
          <div className="divide-y divide-border">
            {rules.map((r, i) => {
              const st = ruleStatus(r, workloads, nodes);
              const color = ruleColor(r, rules, i);
              const flag = st.problem ? (r.strict ? "broken" : "unmet") : null;
              const pair = r.scope_type !== "tag_group" ? (r.workload_ids || []).map((id) => workloadById.get(id)) : [];
              return (
              <div key={r.id} className={`flex items-center justify-between gap-3 py-3 ${editingId === r.id ? "bg-accent/5" : ""}`}>
                <span className="w-1.5 self-stretch rounded shrink-0" style={{ background: color }} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-text flex flex-wrap items-center gap-x-1.5 gap-y-1">
                    {r.scope_type === "tag_group" ? (
                      <span>Guests tagged &quot;{r.tag}&quot;</span>
                    ) : (
                      pair.map((w, k) => (
                        <span key={k} className="inline-flex items-center gap-1.5">
                          {k > 0 && <span>and</span>}
                          {w ? <GuestPill w={w} color={color} flag={flag} /> : <span className="text-muted">a missing guest</span>}
                        </span>
                      ))
                    )}
                    <span>{ruleTail(r)}</span>
                    {editingId === r.id && <span className="text-xs text-accent">editing above</span>}</div>
                  {r.description && <div className="text-xs text-muted">{r.description}</div>}
                  <div className="text-xs text-muted mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span>Right now:</span>
                    {st.members.length === 0 && <span>no guests match</span>}
                    {st.members.map((m) => (
                      <span key={m.id} className="inline-flex items-center gap-1">
                        <GuestPill w={m} color={color} flag={flag} /> on {st.nodeName(m.node_id)}
                      </span>
                    ))}
                  </div>
                </div>
                <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-medium ${st.problem ? (r.strict ? "bg-bad/15 text-bad" : "bg-warn/15 text-warn") : st.members.length < 2 ? "bg-muted/15 text-muted" : "bg-good/15 text-good"}`}>
                  {st.problem ? (r.strict ? "Broken" : "Not met") : st.members.length < 2 ? "Nothing to check" : "Holding"}
                </span>
                {isAdmin && (
                <div className="flex items-center gap-3">
                  <button onClick={() => startEdit(r)} className="text-xs text-accent hover:underline">
                    Edit
                  </button>
                  {confirmDeleteId === r.id ? (
                    <>
                      <span className="text-xs text-muted">Delete this rule?</span>
                      <button onClick={() => deleteRule(r.id)} className="text-xs text-bad font-medium hover:underline">Yes, delete</button>
                      <button onClick={() => setConfirmDeleteId(null)} className="text-xs text-muted hover:underline">Keep</button>
                    </>
                  ) : (
                    <button onClick={() => setConfirmDeleteId(r.id)} className="text-xs text-bad hover:underline">
                      Delete
                    </button>
                  )}
                </div>
                )}
              </div>
              );
            })}
          </div>
        )}
      </Card>
    </>
  );
}
