"use client";

import { useCallback, useEffect, useState } from "react";
import Prerequisites from "@/components/Prerequisites";

type StepItem = { name: string; ok: boolean; detail: string };
type SubStep = {
  key: string; label: string; state: "done" | "todo" | "waiting"; detail: string;
  action: { kind: "generate_keypair" | "anchor" | "href"; label: string; target_id?: string | null; anchor?: string; href?: string } | null;
};
type Step = {
  key: string; number: number; title: string; summary: string;
  state: "done" | "next" | "todo" | "waiting" | "optional";
  items?: StepItem[];
  substeps?: SubStep[];
  action?: { label: string; anchor?: string; href?: string };
};
export type SetupStatus = { steps: Step[]; progress: { done: number; total: number } };
type Check = { group: string; label: string; status: "ok" | "warn" | "fail" | "info"; detail: string; fix: string };
type CheckResult = { checks: Check[]; summary: Record<string, number>; ran_at: string };

const BADGE: Record<Step["state"], { label: string; cls: string }> = {
  done: { label: "Done", cls: "bg-good/15 text-good" },
  next: { label: "Do this next", cls: "bg-accent/15 text-accent" },
  todo: { label: "To do", cls: "bg-warn/15 text-warn" },
  waiting: { label: "Waiting on an earlier step", cls: "bg-surface2 text-muted" },
  optional: { label: "Optional", cls: "bg-surface2 text-muted" },
};
const CHECK_ICON: Record<Check["status"], { icon: string; cls: string }> = {
  ok: { icon: "✓", cls: "text-good" }, warn: { icon: "!", cls: "text-warn" }, fail: { icon: "✕", cls: "text-bad" }, info: { icon: "i", cls: "text-muted" },
};

function go(anchor: string) {
  document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** The Integrations page as a numbered setup guide. Statuses come from /api/setup/status (real data), refreshed every 15 s. */
export default function SetupGuide({ initial }: { initial: SetupStatus }) {
  const [data, setData] = useState(initial);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [subError, setSubError] = useState<string | null>(null);

  async function generateKeypair(targetId: string | null | undefined) {
    if (!targetId) return;
    setBusyKey("keypair");
    setSubError(null);
    try {
      const r = await fetch(`/api/pve-targets/${targetId}/host-maintenance-credential/generate`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
      await refresh();
    } catch (e) {
      setSubError((e as Error).message);
    } finally {
      setBusyKey(null);
    }
  }

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/setup/status", { cache: "no-store" });
      if (r.ok) setData(await r.json());
    } catch { /* keep what we have */ }
  }, []);

  useEffect(() => {
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  async function runCheck() {
    setRunning(true);
    setError(null);
    try {
      const r = await fetch("/api/setup/check", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
      setResult(d);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const { done, total } = data.progress;
  const groups = result ? Array.from(new Set(result.checks.map((c) => c.group))) : [];

  return (
    <div className="mb-6" id="guide">
      <div className="flex items-center gap-3 mb-2">
        <h2 className="text-base font-semibold text-text">Set up PyXie, step by step</h2>
        <a href="/platform/quick-start" className="text-xs text-accent hover:underline">New? Read the quick start</a>
        <span className="ml-auto text-xs text-muted">{done} of {total} done</span>
      </div>
      <Prerequisites collapsible />
      <div className="h-1 rounded bg-border mb-3 overflow-hidden"><div className="h-full bg-good" style={{ width: `${Math.round((done / Math.max(total, 1)) * 100)}%` }} /></div>

      <div className="space-y-2">
        {data.steps.map((s) => {
          const b = BADGE[s.state];
          return (
            <div key={s.key} className={`border rounded-lg p-3 ${s.state === "next" ? "border-accent" : "border-border"} ${s.state === "waiting" ? "opacity-70" : ""}`}>
              <div className="flex items-center gap-3">
                <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium ${s.state === "done" ? "bg-good/15 text-good" : s.state === "next" ? "bg-accent/15 text-accent" : "bg-surface2 text-muted"}`}>
                  {s.state === "done" ? "✓" : s.number}
                </span>
                <span className="text-sm font-medium text-text">{s.number}. {s.title}</span>
                <span className={`ml-auto text-[11px] px-2 py-0.5 rounded ${b.cls}`}>{b.label}</span>
              </div>
              <p className="text-xs text-muted mt-1.5 ml-9">{s.summary}</p>
              {s.items && s.items.length > 0 && (
                <ul className="mt-1.5 ml-9 flex flex-wrap gap-1.5">
                  {s.items.map((i) => (
                    <li key={i.name} className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-surface2">
                      <span className={i.ok ? "text-good" : "text-warn"}>{i.ok ? "✓" : "○"}</span> {i.name}: {i.detail}
                    </li>
                  ))}
                </ul>
              )}
              {s.substeps && (
                <ol className="mt-2 ml-9 space-y-1.5">
                  {s.substeps.map((ss, i) => (
                    <li key={ss.key} className={`flex items-start gap-2 text-xs ${ss.state === "waiting" ? "opacity-60" : ""}`}>
                      <span className={`w-4 text-center font-medium ${ss.state === "done" ? "text-good" : "text-muted"}`}>{ss.state === "done" ? "✓" : String.fromCharCode(97 + i) + ")"}</span>
                      <span className="flex-1">
                        <span className="text-text">{ss.label}.</span> <span className="text-muted">{ss.detail}</span>
                        {ss.action && (
                          <span className="ml-2">
                            {ss.action.kind === "generate_keypair" ? (
                              <button type="button" disabled={busyKey === "keypair"} onClick={() => generateKeypair(ss.action!.target_id)}
                                className="px-2 py-0.5 rounded border border-border hover:bg-surface2 disabled:opacity-50">{busyKey === "keypair" ? "Generating…" : ss.action.label}</button>
                            ) : ss.action.kind === "href" ? (
                              <a href={ss.action.href} className="px-2 py-0.5 rounded border border-border hover:bg-surface2">{ss.action.label} →</a>
                            ) : (
                              <button type="button" onClick={() => go(ss.action!.anchor!)} className="px-2 py-0.5 rounded border border-border hover:bg-surface2">{ss.action.label} ↓</button>
                            )}
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                  {subError && <li className="text-xs text-bad">{subError}</li>}
                </ol>
              )}
              {s.key === "check" ? (
                <div className="ml-9 mt-2">
                  <button type="button" onClick={runCheck} disabled={running}
                    className="px-2.5 py-1 rounded border border-border text-xs hover:bg-surface2 disabled:opacity-50">
                    {running ? "Checking… (up to 30 s)" : result ? "Run check again" : "Run check"}
                  </button>
                  {error && <span className="ml-3 text-xs text-bad">{error}</span>}
                </div>
              ) : s.action ? (
                <div className="ml-9 mt-2">
                  {s.action.href ? (
                    <a href={s.action.href} className="inline-block px-2.5 py-1 rounded border border-border text-xs hover:bg-surface2">{s.action.label} →</a>
                  ) : (
                    <button type="button" onClick={() => go(s.action!.anchor!)} className="px-2.5 py-1 rounded border border-border text-xs hover:bg-surface2">{s.action.label} ↓</button>
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {result && (
        <div className="mt-3 border border-border rounded-lg p-3" id="check">
          <div className="flex items-center gap-3 text-xs text-muted mb-2">
            <span className="text-sm font-medium text-text">Check results</span>
            <span>{result.summary.fail} failed · {result.summary.warn} to look at · {result.summary.ok} fine</span>
            <span className="ml-auto">{new Date(result.ran_at).toLocaleTimeString()}</span>
          </div>
          {groups.map((g) => (
            <div key={g} className="mb-2">
              <div className="text-[11px] uppercase tracking-wide text-muted mb-1">{g}</div>
              <ul className="space-y-1">
                {result.checks.filter((c) => c.group === g).map((c, i) => (
                  <li key={g + i} className="text-xs flex gap-2">
                    <span className={`w-4 text-center font-medium ${CHECK_ICON[c.status].cls}`}>{CHECK_ICON[c.status].icon}</span>
                    <span><span className="text-text">{c.label}.</span> <span className="text-muted">{c.detail}</span>{c.fix && c.status !== "ok" && <span className="text-muted"> {c.fix}</span>}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
