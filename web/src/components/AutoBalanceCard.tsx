"use client";

import { useState } from "react";
import { Card, CardTitle } from "@/components/Card";
import { useMe } from "@/lib/useMe";

export type AutoBalanceView = {
  cluster_id: string;
  cluster_name: string;
  config: {
    mode: string; level: string; metric: string; node_ids: string[];
    windows: { days: number[]; start: string; end: string }[]; paused_until: string | null;
    ack?: { by: string; at: string; aggressive: boolean } | null;
  };
  writes_enabled?: boolean;
  locked_guests?: { do_not_move: number; pinned: number };
  state: { last_checked_at?: string; last_run_at?: string; last_result?: string; last_reason?: string | null; moves?: number; balance_score?: number };
  presets: Record<string, { trigger_score: number; min_benefit: number; max_moves: number; cluster_cooldown_hours: number; guest_cooldown_hours: number }>;
  nodes: { id: string; name: string }[];
  balance_score: number | null;
  resolved_metric: string;
  pending_operation_id: string | null;
  waiting_operation_id?: string | null;
  history: { operation_id: string; created_at: string; status: string; moves: number; level: string | null; balance_score: number | null; auto_approved?: boolean }[];
};

const LEVELS: { id: string; label: string }[] = [
  { id: "conservative", label: "Conservative" },
  { id: "moderate", label: "Moderate" },
  { id: "aggressive", label: "Aggressive" },
];
const METRICS = [
  { id: "most_limited", label: "Most limited resource" },
  { id: "memory", label: "Memory" },
  { id: "cpu", label: "CPU" },
  { id: "both", label: "Both (average)" },
];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function hours(h: number) {
  return h < 1 ? `${Math.round(h * 60)} min` : `${h} h`;
}

function ago(iso?: string) {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : m < 90 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}

function Cluster({ initial }: { initial: AutoBalanceView }) {
  const [view, setView] = useState(initial);
  const [cfg, setCfg] = useState(initial.config);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [ackOk, setAckOk] = useState(false);
  const [ackAggressive, setAckAggressive] = useState(false);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const preset = view.presets[cfg.level];
  const dirty = JSON.stringify({ ...cfg, paused_until: null }) !== JSON.stringify({ ...view.config, paused_until: null });
  const paused = !!view.config.paused_until && new Date(view.config.paused_until).getTime() > Date.now();
  const auto = cfg.mode === "auto_approve";
  const savedAuto = view.config.mode === "auto_approve";
  // A new auto-approve, or a switch to Aggressive, needs the admin's confirmation; editing other settings of a confirmed cluster does not.
  const needsAck = auto && !(savedAuto && view.config.ack && (cfg.level !== "aggressive" || view.config.ack.aggressive));
  const ackReady = !needsAck || (ackOk && (cfg.level !== "aggressive" || ackAggressive));
  const locked = view.locked_guests ? view.locked_guests.do_not_move + view.locked_guests.pinned : 0;

  async function call(url: string, method: string, body?: unknown) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Save failed");
      setView(data as AutoBalanceView);
      setCfg((data as AutoBalanceView).config);
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const setWindow = (i: number, patch: Partial<AutoBalanceView["config"]["windows"][number]>) =>
    setCfg((c) => ({ ...c, windows: c.windows.map((w, j) => (j === i ? { ...w, ...patch } : w)) }));

  const sel = "bg-surface2 border border-border rounded px-2 py-1 text-sm";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-text font-medium">Cluster {view.cluster_name}</span>
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${paused || view.config.mode === "off" ? "bg-muted/15 text-muted" : savedAuto ? "bg-warn/15 text-warn" : "bg-good/15 text-good"}`}>
          {view.config.mode === "off" ? "Off" : paused ? "Paused" : savedAuto ? "Automatic: approves on its own" : "Recommend only"}
        </span>
        {view.balance_score !== null && <span className="text-muted">balance score now {view.balance_score}</span>}
      </div>

      <div>
        <div className="text-xs text-muted mb-1.5">Mode</div>
        <div className="flex flex-wrap gap-2">
          {[["off", "Off"], ["recommend", "Recommend only"], ["auto_approve", "Auto-approve"]].map(([id, label]) => (
            <button key={id} disabled={!isAdmin} onClick={() => setCfg({ ...cfg, mode: id })}
              className={`px-3 py-1.5 rounded border text-sm ${cfg.mode === id ? "border-accent bg-accent/10 text-text" : "border-border text-muted hover:text-text"}`}>{label}</button>
          ))}
        </div>
        <p className="text-xs text-muted mt-1.5">
          {auto
            ? "Auto-approve: when the cluster drifts out of balance, PyXie prepares a plan with ONE move, approves it itself and live-migrates that guest, then re-checks the cluster before considering another."
            : "Recommend only: when the cluster drifts out of balance, PyXie prepares a Balance Load plan and waits. It never approves or moves anything by itself; you review and approve it here like any other plan."}
        </p>
        {auto && preset && (
          <div className="mt-3 rounded border border-warn/50 bg-warn/10 px-3 py-3 text-sm text-text space-y-2">
            <div className="font-medium text-warn">Before you turn this on: PyXie will move running guests without asking you.</div>
            <ul className="list-disc pl-5 text-xs text-muted space-y-1">
              <li>It live-migrates <span className="text-text">one guest at a time</span>. After each move finishes it waits at least {hours(preset.cluster_cooldown_hours)}, then plans the next move from fresh numbers. A guest it moved is left alone for {hours(preset.guest_cooldown_hours)}.</li>
              <li>It only acts when the balance score is below {preset.trigger_score}, and only for moves worth {preset.min_benefit}+ points.</li>
              <li>{cfg.windows.length === 0 ? "It may act at any time of day." : `It acts only inside the time window${cfg.windows.length === 1 ? "" : "s"} below.`}</li>
              <li>{locked > 0 ? `${locked} running guest${locked === 1 ? " is" : "s are"} marked Do not move or pinned to their host and will never be touched.` : "No guest is marked Do not move or pinned to its host right now, so every running VM can be moved."}</li>
              <li>It never moves guests off a node in maintenance, never approves a plan that also changes storage, and never approves a move that would leave any node above 80% memory. Those plans wait for you instead.</li>
              <li>It stops at the first failed or blocked move, alerts you, and switches this cluster back to Recommend only. Nothing retries by itself.</li>
              <li>The global write switch and the Pause button stop it at any time.{view.writes_enabled === false ? " The write switch is OFF right now, so plans will be prepared but left for you." : ""}</li>
              {cfg.level === "conservative" && <li>The Conservative level also never moves a guest whose downtime tolerance is Low.</li>}
            </ul>
            {needsAck && (
              <div className="space-y-1.5 pt-1">
                <label className="flex items-start gap-2 text-sm">
                  <input type="checkbox" className="mt-1" checked={ackOk} onChange={(e) => setAckOk(e.target.checked)} />
                  <span>I understand that PyXie will live-migrate running guests on its own in this cluster.</span>
                </label>
                {cfg.level === "aggressive" && (
                  <label className="flex items-start gap-2 text-sm text-bad">
                    <input type="checkbox" className="mt-1" checked={ackAggressive} onChange={(e) => setAckAggressive(e.target.checked)} />
                    <span>Aggressive: it may act every {hours(preset.cluster_cooldown_hours)} and moves guests more readily. I accept that.</span>
                  </label>
                )}
              </div>
            )}
            {!needsAck && view.config.ack && <div className="text-xs text-muted">Confirmed by {view.config.ack.by} on {new Date(view.config.ack.at).toLocaleString()}.</div>}
          </div>
        )}
      </div>

      <div>
        <div className="text-xs text-muted mb-1.5">How aggressive</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          {LEVELS.map((l) => {
            const p = view.presets[l.id];
            return (
              <button key={l.id} disabled={!isAdmin} onClick={() => setCfg({ ...cfg, level: l.id })}
                className={`text-left rounded border px-3 py-2 ${cfg.level === l.id ? "border-accent bg-accent/10" : "border-border hover:bg-surface2/60"}`}>
                <div className="text-sm font-medium text-text">{l.label}</div>
                <div className="text-xs text-muted mt-0.5">
                  Acts when balance score is below {p.trigger_score}. Only moves worth {p.min_benefit}+ points, up to {p.max_moves} per plan, at most one plan every {hours(p.cluster_cooldown_hours)}; a moved guest is left alone for {hours(p.guest_cooldown_hours)}.
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-muted mb-1.5">Balance on</div>
          <select className={sel} disabled={!isAdmin} value={cfg.metric} onChange={(e) => setCfg({ ...cfg, metric: e.target.value })}>
            {METRICS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
          <p className="text-xs text-muted mt-1.5">
            {cfg.metric === "most_limited"
              ? `Balances on whichever of memory and CPU is running hotter on the busiest node. Right now: ${view.resolved_metric}.`
              : cfg.metric === "both" ? "Treats memory and CPU as one average." : `Balances on ${cfg.metric} only.`}
          </p>
        </div>
        <div>
          <div className="text-xs text-muted mb-1.5">Take guests from</div>
          <div className="flex flex-wrap gap-2">
            <button disabled={!isAdmin} onClick={() => setCfg({ ...cfg, node_ids: [] })}
              className={`px-2.5 py-1 rounded border text-sm ${cfg.node_ids.length === 0 ? "border-accent bg-accent/10 text-text" : "border-border text-muted"}`}>Whole cluster</button>
            {view.nodes.map((n) => {
              const on = cfg.node_ids.includes(n.id);
              return (
                <button key={n.id} disabled={!isAdmin}
                  onClick={() => setCfg({ ...cfg, node_ids: on ? cfg.node_ids.filter((x) => x !== n.id) : [...cfg.node_ids, n.id] })}
                  className={`px-2.5 py-1 rounded border text-sm ${on ? "border-accent bg-accent/10 text-text" : "border-border text-muted"}`}>{n.name}</button>
              );
            })}
          </div>
          <p className="text-xs text-muted mt-1.5">Guests marked &ldquo;do not move&rdquo; on their workload page are always left alone.</p>
        </div>
      </div>

      <div>
        <div className="text-xs text-muted mb-1.5">When it may prepare plans</div>
        {cfg.windows.length === 0 && <p className="text-sm text-muted mb-1.5">Any time.</p>}
        <div className="space-y-2">
          {cfg.windows.map((w, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              {DAYS.map((d, di) => (
                <button key={d} disabled={!isAdmin}
                  onClick={() => setWindow(i, { days: w.days.includes(di) ? w.days.filter((x) => x !== di) : [...w.days, di].sort() })}
                  className={`px-2 py-0.5 rounded border text-xs ${w.days.includes(di) ? "border-accent bg-accent/10 text-text" : "border-border text-muted"}`}>{d}</button>
              ))}
              <input type="time" className={sel} value={w.start} disabled={!isAdmin} onChange={(e) => setWindow(i, { start: e.target.value })} />
              <span className="text-muted text-xs">to</span>
              <input type="time" className={sel} value={w.end} disabled={!isAdmin} onChange={(e) => setWindow(i, { end: e.target.value })} />
              {isAdmin && <button onClick={() => setCfg({ ...cfg, windows: cfg.windows.filter((_, j) => j !== i) })} className="text-xs text-bad hover:underline">Remove</button>}
            </div>
          ))}
        </div>
        {isAdmin && (
          <button onClick={() => setCfg({ ...cfg, windows: [...cfg.windows, { days: [0, 1, 2, 3, 4, 5, 6], start: "22:00", end: "05:00" }] })}
            className="mt-2 text-xs text-accent hover:underline">+ Add a time window</button>
        )}
        <p className="text-xs text-muted mt-1.5">Times use the server&apos;s timezone (Settings). A window that ends earlier than it starts runs past midnight.</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        {isAdmin && (
          <button disabled={busy || !dirty || !ackReady} onClick={() => call(`/api/auto-balance/${view.cluster_id}`, "PUT", { mode: cfg.mode, level: cfg.level, metric: cfg.metric, node_ids: cfg.node_ids, windows: cfg.windows, acknowledged: needsAck && ackOk, acknowledged_aggressive: needsAck && ackAggressive })}
            className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50">
            {busy ? "Saving…" : "Save automatic balancing"}
          </button>
        )}
        {isAdmin && view.config.mode !== "off" && (paused
          ? <button disabled={busy} onClick={() => call(`/api/auto-balance/${view.cluster_id}/pause`, "DELETE")} className="px-3 py-1.5 rounded text-sm border border-border text-text hover:bg-surface2">Resume</button>
          : <button disabled={busy} onClick={() => call(`/api/auto-balance/${view.cluster_id}/pause`, "POST", { hours: 24 })} className="px-3 py-1.5 rounded text-sm border border-border text-text hover:bg-surface2">Pause for 24 h</button>)}
        {saved && !dirty && <span className="text-xs text-good">Saved</span>}
        {error && <span className="text-xs text-bad">{error}</span>}
      </div>

      {view.config.mode !== "off" && preset && (
        <div className="rounded border border-border px-3 py-2 text-sm bg-surface2/40">
          <div className="text-muted">
            Last checked {ago(view.state.last_checked_at)}
            {view.state.last_result === "skipped" && view.state.last_reason ? `: nothing to do (${view.state.last_reason}).` : ""}
            {view.state.last_result === "plan_created" ? `: prepared a plan with ${view.state.moves} move(s).${view.state.last_reason ? ` ${view.state.last_reason}.` : ""}` : ""}
            {view.state.last_result === "plan_auto_approved" ? `: approved and started ${view.state.moves} move. It re-checks after the move finishes.` : ""}
          </div>
          {view.state.last_result === "stopped" && <div className="text-bad mt-1">Auto-approve stopped: {view.state.last_reason}. This cluster is back to Recommend only.</div>}
          {view.pending_operation_id && <div className="text-warn mt-1">A plan from automatic balancing is waiting for your approval below.</div>}
        </div>
      )}

      {view.history.length > 0 && (
        <div>
          <div className="text-xs text-muted mb-1.5">Recent automatic plans</div>
          <ul className="text-sm divide-y divide-border">
            {view.history.map((h) => (
              <li key={h.operation_id} className="flex items-center justify-between py-1.5">
                <span className="text-text">{ago(h.created_at)} · {h.moves} move(s){h.level ? ` · ${h.level}` : ""}{h.balance_score !== null ? ` · score ${h.balance_score}` : ""}</span>
                <span className="text-xs text-muted">{h.auto_approved ? "auto-approved · " : ""}{h.status.replace(/_/g, " ")}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function AutoBalanceCard({ clusters }: { clusters: AutoBalanceView[] }) {
  if (clusters.length === 0) return null;
  return (
    <Card>
      <CardTitle>Automatic balancing</CardTitle>
      <p className="text-sm text-muted mb-4">
        Let PyXie watch the cluster and prepare a Balance Load plan when it drifts out of balance. Everything it prepares goes through the same checks as a plan you start yourself: affinity rules, memory headroom, CPU compatibility, HA, maintenance mode, and the global write switch.
      </p>
      <div className="space-y-6 divide-y divide-border">
        {clusters.map((c) => <Cluster key={c.cluster_id} initial={c} />)}
      </div>
    </Card>
  );
}
