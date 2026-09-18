"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { NotificationCatalog, NotificationRule } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Draft = Omit<NotificationRule, "id"> & { id?: string };

const BLANK: Draft = {
  name: "",
  enabled: true,
  categories: [],
  min_severity: "critical",
  send_recovery: true,
  recipients: [],
  include_admins: false,
};

function parseAddresses(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((a) => a.trim())
    .filter(Boolean);
}

function describe(rule: NotificationRule, catalog: NotificationCatalog): string {
  const cats = rule.categories.length
    ? rule.categories.map((k) => catalog.categories.find((c) => c.key === k)?.label ?? k).join(", ")
    : "All events";
  const sev = rule.min_severity === "critical" ? "critical only" : "warning and above";
  return `${cats} · ${sev}${rule.send_recovery ? " · with recovery notices" : ""}`;
}

export default function NotificationRules({
  initial,
  catalog,
}: {
  initial: NotificationRule[];
  catalog: NotificationCatalog;
}) {
  const router = useRouter();
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [editing, setEditing] = useState<Draft | null>(null);
  const [recipientText, setRecipientText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  function startEdit(rule: Draft) {
    setEditing(rule);
    setRecipientText(rule.recipients.join(", "));
    setError(null);
  }

  async function call(url: string, method: string, body?: unknown) {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // The API's 422 detail arrives as a JSON string inside `error`.
      let msg = data.error || `Request failed (${res.status})`;
      try {
        msg = JSON.parse(msg).detail ?? msg;
      } catch {}
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setPending(true);
    setError(null);
    try {
      const body = { ...editing, recipients: parseAddresses(recipientText) };
      delete (body as Partial<Draft>).id;
      await call(editing.id ? `/api/notification-rules/${editing.id}` : "/api/notification-rules", editing.id ? "PUT" : "POST", body);
      setEditing(null);
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function toggle(rule: NotificationRule) {
    setError(null);
    try {
      const { id, ...rest } = rule;
      await call(`/api/notification-rules/${id}`, "PUT", { ...rest, enabled: !rule.enabled });
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove(rule: NotificationRule) {
    if (!confirm(`Delete the rule "${rule.name}"? Emails it was sending will stop.`)) return;
    setError(null);
    try {
      await call(`/api/notification-rules/${rule.id}`, "DELETE");
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function sendTest(rule: NotificationRule) {
    setTestResult((t) => ({ ...t, [rule.id]: "Sending…" }));
    try {
      const data = await call(`/api/notification-rules/${rule.id}/test`, "POST");
      setTestResult((t) => ({
        ...t,
        [rule.id]: data.status === "failed" && !data.sent?.length
          ? `Failed: ${data.error || (data.errors || []).join("; ")}`
          : `Sent to ${data.sent.join(", ")}${data.errors?.length ? ` — failed: ${data.errors.join("; ")}` : ""}`,
      }));
    } catch (err) {
      setTestResult((t) => ({ ...t, [rule.id]: `Failed: ${(err as Error).message}` }));
    }
  }

  const allCategories = editing ? editing.categories.length === 0 : false;

  return (
    <div>
      {initial.length === 0 && !editing && (
        <div className="text-sm text-muted py-4">
          No rules yet — nothing will be emailed. Add a rule to choose which events send email, and to whom.
        </div>
      )}
      <div className="divide-y divide-border">
        {initial.map((rule) => (
          <div key={rule.id} className="py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm text-text font-medium">
                  {rule.name}
                  <span
                    className={`text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 ${
                      rule.enabled ? "bg-good/15 text-good" : "bg-muted/15 text-muted"
                    }`}
                  >
                    {rule.enabled ? "On" : "Off"}
                  </span>
                </div>
                <div className="text-xs text-muted mt-0.5">{describe(rule, catalog)}</div>
                <div className="text-xs text-muted">
                  To: {[...rule.recipients, ...(rule.include_admins ? ["all admin users"] : [])].join(", ") || "—"}
                </div>
                {testResult[rule.id] && (
                  <div className={`text-xs mt-1 ${testResult[rule.id].startsWith("Failed") ? "text-bad" : "text-good"}`}>
                    {testResult[rule.id]}
                  </div>
                )}
              </div>
              {isAdmin && (
                <div className="flex shrink-0 gap-2 text-xs">
                  <button className="btn" onClick={() => toggle(rule)}>
                    {rule.enabled ? "Turn off" : "Turn on"}
                  </button>
                  <button className="btn" onClick={() => sendTest(rule)}>Send test</button>
                  <button className="btn" onClick={() => startEdit(rule)}>Edit</button>
                  <button className="btn text-bad" onClick={() => remove(rule)}>Delete</button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {isAdmin && !editing && (
        <button className="btn mt-3 text-sm" onClick={() => startEdit({ ...BLANK })}>
          + Add rule
        </button>
      )}
      {error && !editing && <div className="text-xs text-bad mt-2">{error}</div>}

      {editing && (
        <form onSubmit={save} className="mt-4 border border-border rounded-lg p-4 space-y-4 max-w-xl">
          <div className="text-sm font-medium text-text">{editing.id ? "Edit rule" : "New rule"}</div>
          <label className="block text-xs text-muted space-y-1">
            <span>Name</span>
            <input
              className="input"
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="e.g. On-call critical alerts"
              required
            />
          </label>

          <fieldset className="space-y-1">
            <legend className="text-xs text-muted mb-1">Send an email when…</legend>
            <label className="flex items-center gap-2 text-sm text-text">
              <input
                type="checkbox"
                checked={allCategories}
                onChange={(e) => setEditing({ ...editing, categories: e.target.checked ? [] : catalog.categories.map((c) => c.key) })}
              />
              Any event
            </label>
            {!allCategories && (
              <div className="pl-5 space-y-1">
                {catalog.categories.map((c) => (
                  <label key={c.key} className="flex items-start gap-2 text-sm text-text">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={editing.categories.includes(c.key)}
                      onChange={(e) =>
                        setEditing({
                          ...editing,
                          categories: e.target.checked
                            ? [...editing.categories, c.key]
                            : editing.categories.filter((k) => k !== c.key),
                        })
                      }
                    />
                    <span>
                      {c.label}
                      <span className="block text-[11px] text-muted">{c.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>

          <label className="block text-xs text-muted space-y-1">
            <span>Minimum severity</span>
            <select
              className="input"
              value={editing.min_severity}
              onChange={(e) => setEditing({ ...editing, min_severity: e.target.value as "warning" | "critical" })}
            >
              <option value="critical">Critical only</option>
              <option value="warning">Warning and above</option>
            </select>
          </label>

          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={editing.send_recovery}
              onChange={(e) => setEditing({ ...editing, send_recovery: e.target.checked })}
            />
            Also email when the problem clears (recovery notice)
          </label>

          <label className="block text-xs text-muted space-y-1">
            <span>Recipients (comma or space separated)</span>
            <textarea
              className="input"
              rows={2}
              value={recipientText}
              onChange={(e) => setRecipientText(e.target.value)}
              placeholder="oncall@example.com, you@example.com"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={editing.include_admins}
              onChange={(e) => setEditing({ ...editing, include_admins: e.target.checked })}
            />
            Also send to every active admin user
          </label>
          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={editing.enabled}
              onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })}
            />
            Rule is on
          </label>

          {error && <div className="text-xs text-bad">{error}</div>}
          <div className="flex gap-3">
            <button
              type="submit"
              disabled={pending}
              className="px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
            >
              {pending ? "Saving…" : "Save rule"}
            </button>
            <button type="button" className="btn text-sm" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      <style jsx>{`
        .input {
          width: 100%;
          background: #0b0e14;
          border: 1px solid #232a38;
          border-radius: 6px;
          padding: 6px 8px;
          font-size: 0.875rem;
          color: #e6e9ef;
        }
        .btn {
          padding: 4px 10px;
          border-radius: 6px;
          border: 1px solid #232a38;
          background: transparent;
          color: inherit;
        }
        .btn:hover {
          background: rgba(255, 255, 255, 0.05);
        }
      `}</style>
    </div>
  );
}
