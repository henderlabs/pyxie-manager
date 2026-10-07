"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Card, CardTitle } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/format";
import type { UpdatesResponse, UpdateStep } from "@/lib/updates";

const STEP_ICON: Record<UpdateStep["status"], { icon: string; cls: string }> = {
  done: { icon: "✓", cls: "text-good" },
  running: { icon: "●", cls: "text-warn animate-pulse" },
  failed: { icon: "✕", cls: "text-bad" },
  skipped: { icon: "–", cls: "text-muted" },
  pending: { icon: "○", cls: "text-muted" },
};

const BTN = "px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50 disabled:cursor-not-allowed";
const BTN2 = "px-3 py-1.5 rounded text-sm border border-border text-muted hover:text-text disabled:opacity-50";

export default function UpdatesPanel({ initial }: { initial: UpdatesResponse }) {
  const [data, setData] = useState<UpdatesResponse>(initial);
  const [offline, setOffline] = useState(false);
  const [confirm, setConfirm] = useState<"update" | "rollback" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const startedWith = useRef(initial.current);
  const checkedAtBefore = useRef(initial.status.checked_at);
  const logRef = useRef<HTMLDivElement>(null);

  const running = data.state.state === "running";

  // The click is acknowledged at once: "asked" bridges the gap until the updater starts a run (state.started_at changes).
  const [asked, setAsked] = useState<{ action: "update" | "rollback"; version: string | null; at: number; stateStart: string | null } | null>(null);
  const [now, setNow] = useState(Date.now());
  // A pending Check now is not an update: only updates and restores get the waiting card.
  const pending = data.pending && (data.pending.action === "update" || data.pending.action === "rollback") ? data.pending : null;
  const askedLive = asked !== null && !running && data.state.started_at === asked.stateStart && now - asked.at < 3 * 60 * 1000;
  const waiting = !running && (pending !== null || askedLive);
  const waitAction = pending?.action ?? asked?.action ?? "update";
  const waitVersion = pending?.version ?? asked?.version ?? null;
  const waitSince = pending?.requested_at ? new Date(pending.requested_at).getTime() : asked?.at ?? now;
  const askedStale = asked !== null && !running && !pending && data.state.started_at === asked.stateStart && now - asked.at >= 3 * 60 * 1000;

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/system/updates", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
      setOffline(false);
    } catch {
      setOffline(true); // the app restarts during an update; keep trying
    }
  }, []);

  useEffect(() => {
    const iv = setInterval(refresh, running || offline || checking || waiting ? 2000 : 20000);
    return () => clearInterval(iv);
  }, [refresh, running, offline, checking, waiting]);

  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [waiting]);

  // A new version is running: reload so the page's own code is the new version too.
  useEffect(() => {
    if (data.current !== startedWith.current && data.state.state !== "running") {
      const t = setTimeout(() => window.location.reload(), 2500);
      return () => clearTimeout(t);
    }
  }, [data.current, data.state.state]);

  useEffect(() => {
    if (checking && data.status.checked_at !== checkedAtBefore.current) setChecking(false);
  }, [checking, data.status.checked_at]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [data.log.length]);

  async function post(path: string, body?: object) {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`/api/system/updates/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
      await refresh();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const s = data.status;
  const latest = s.latest;
  const instance = s.instance || "this server";
  const canAct = data.installed && data.blocked_by.length === 0;
  const showProgress = data.state.state != null && !waiting;
  const rb = data.rollback;

  // Layout: Version beside the live progress (so an update is visible without scrolling past the release
  // notes); the release notes move below it, beside the log. With nothing in progress, Version sits beside
  // the release notes as before.
  const progressVisible = showProgress || waiting;
  const versionCard = (
        <Card>
          <CardTitle>Version</CardTitle>
          <dl className="text-sm space-y-1">
            <div className="flex justify-between"><dt className="text-muted">This server</dt><dd>{instance}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Running</dt><dd>v{data.current}</dd></div>
            <div className="flex justify-between">
              <dt className="text-muted">Latest release</dt>
              <dd>{s.update_available ? <span className="px-2 py-0.5 rounded text-xs font-medium bg-accent/15 text-accent">v{latest}</span> : latest ? <span className="text-good">v{latest} · up to date</span> : "—"}</dd>
            </div>
            <div className="flex justify-between items-center">
              <dt className="text-muted">Last checked</dt>
              <dd className="flex items-center gap-3">
                {s.checked_at ? formatRelativeTime(s.checked_at) : "never"}
                <button
                  type="button"
                  className="text-accent hover:underline disabled:opacity-50"
                  disabled={busy || checking || !data.installed}
                  onClick={async () => {
                    checkedAtBefore.current = data.status.checked_at;
                    if (await post("check")) setChecking(true);
                  }}
                >
                  {checking ? "Checking…" : "Check now"}
                </button>
              </dd>
            </div>
            {s.error && <div className="text-warn text-xs">The last check could not reach GitHub: {s.error}</div>}
            <div className="flex justify-between"><dt className="text-muted">Running operations</dt><dd>{data.in_flight_operations === 0 ? <span className="text-good">none, safe to update</span> : <span className="text-warn">{data.in_flight_operations}</span>}</dd></div>
          </dl>

          {s.update_available && !running && !waiting && (
            <div className="mt-4">
              {confirm !== "update" ? (
                <div>
                  <button type="button" className={BTN} disabled={!canAct || busy} onClick={() => setConfirm("update")}>
                    Update to v{latest}
                  </button>
                  {data.blocked_by.length > 0 && <ul className="mt-2 text-xs text-warn list-disc pl-4">{data.blocked_by.map((r) => <li key={r}>{r}</li>)}</ul>}
                </div>
              ) : (
                <div className="border border-accent/40 rounded p-3 bg-accent/5 text-sm">
                  <div className="font-medium mb-1">Update {instance} from v{data.current} to v{latest}?</div>
                  <ul className="list-disc pl-4 text-muted text-xs space-y-0.5 mb-3">
                    <li>A database backup is taken and checked first.</li>
                    <li>The site is unavailable for about 1 to 2 minutes while it restarts.</li>
                    <li>If the new version does not come up healthy, the previous version is restored automatically.</li>
                  </ul>
                  <button type="button" className={BTN} disabled={busy} onClick={async () => { if (await post("apply", { version: latest })) { setAsked({ action: "update", version: latest ?? null, at: Date.now(), stateStart: data.state.started_at ?? null }); setConfirm(null); } }}>
                    {busy ? "Starting…" : "Update now"}
                  </button>
                  <button type="button" className={`${BTN2} ml-2`} onClick={() => setConfirm(null)}>Cancel</button>
                </div>
              )}
            </div>
          )}
        </Card>
  );
  const whatsNewCard = (
        <Card>
          <CardTitle>{s.update_available ? `What's new (since v${data.current})` : "Recent releases"}</CardTitle>
          {s.update_available && s.releases && s.releases.length > 0 ? (
            <div className="space-y-3 text-sm">
              {[...s.releases].reverse().map((r) => (
                <div key={r.version}>
                  <div className="font-medium">v{r.version} <span className="text-muted text-xs font-normal">{r.date}</span></div>
                  <div className="text-muted whitespace-pre-wrap text-xs">{r.notes || "No release notes."}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted">You are on the latest release.</div>
          )}
        </Card>
  );
  const waitingCard = (
        <Card>
          <CardTitle>
            {waitAction === "rollback" ? "Restoring" : "Updating"}{waitVersion ? ` to v${waitVersion}` : ""}{" "}
            <span className="normal-case"><StatusBadge status="running" /></span>
          </CardTitle>
          <div className="flex items-center gap-2 text-sm">
            <span className="inline-block w-3.5 h-3.5 rounded-full border-2 border-accent border-t-transparent animate-spin" />
            <span>Request sent. Waiting for the updater to start ({Math.max(0, Math.floor((now - waitSince) / 1000))}s)…</span>
          </div>
          <p className="mt-2 text-xs text-muted">The updater checks for requests every couple of seconds; the first step appears here as soon as it begins. You can leave this page open.</p>
        </Card>
  );
  const progressCard = (
          <Card>
            <CardTitle>
              {data.state.action === "rollback" ? "Restoring" : "Updating"} v{data.state.from} → v{data.state.to}
              {" "}
              <span className="normal-case"><StatusBadge status={running ? "running" : data.state.state === "success" ? "success" : data.state.state === "rolled_back" ? "warning" : "failed"} /></span>
            </CardTitle>
            <div className="space-y-1 text-sm">
              {(data.state.steps || []).map((st) => (
                <div key={st.key + st.label} className="flex gap-2">
                  <span className={`w-4 text-center ${STEP_ICON[st.status].cls}`}>{STEP_ICON[st.status].icon}</span>
                  <span className={st.status === "pending" ? "text-muted" : ""}>{st.label}</span>
                </div>
              ))}
            </div>
            {data.state.message && (
              <div className={`mt-3 text-sm ${data.state.state === "success" ? "text-good" : data.state.state === "running" ? "text-muted" : "text-warn"}`}>{data.state.message}</div>
            )}
            {data.state.backup && <div className="mt-1 text-xs text-muted">Database backup: <code>{data.state.backup}</code></div>}
          </Card>

  );
  const logCard = (
          <Card>
            <CardTitle>Live log</CardTitle>
            <div ref={logRef} className="max-h-72 overflow-y-auto bg-canvas border border-border rounded p-2 font-mono text-[11px] leading-5 text-muted whitespace-pre-wrap">
              {data.log.length ? data.log.join("\n") : "Nothing yet."}
            </div>
          </Card>

  );

  return (
    <div className="space-y-4">
      {offline && (
        <div className="px-3 py-2 rounded border border-warn/40 bg-warn/10 text-warn text-sm">
          The site is restarting. This page reconnects by itself.
        </div>
      )}
      {error && <div className="px-3 py-2 rounded border border-bad/40 bg-bad/10 text-bad text-sm">{error}</div>}

      {!data.installed && (
        <Card>
          <CardTitle>Updater not installed</CardTitle>
          <div className="text-sm text-muted">
            This server does not have the update helper yet. On the server, run <code>bash ops/docker/install-updater.sh</code> (see docs/updates.md); this page then shows release status here.
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
        {versionCard}
        {progressVisible ? (waiting ? waitingCard : progressCard) : whatsNewCard}
      </div>
      {askedStale && (
        <div className="px-3 py-2 rounded border border-warn/40 bg-warn/10 text-warn text-sm">
          The updater has not started the request after 3 minutes. Check that its cron job is installed (docs/updates.md) and look at update/update.log on the server.
        </div>
      )}

      {progressVisible && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
          {whatsNewCard}
          {logCard}
        </div>
      )}

      <Card>
        <CardTitle>Update history</CardTitle>
        {data.history.length === 0 ? (
          <div className="text-sm text-muted">No updates have been run from here yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">When</th><th className="pr-3">Change</th><th className="pr-3">Result</th><th>By</th></tr></thead>
            <tbody>
              {data.history.map((h) => (
                <tr key={h.id} className="border-t border-border">
                  <td className="py-1.5 pr-3 text-muted" title={new Date(h.finished_at).toLocaleString()}>{formatRelativeTime(h.finished_at)}</td>
                  <td className="pr-3">{h.action === "rollback" ? "Restored" : "Updated"} v{h.from_version} → v{h.to_version}{h.migrated ? <span className="text-muted text-xs ml-1">(database changed)</span> : null}</td>
                  <td className="pr-3">{h.rolled_back ? <span className="text-muted">undone</span> : <StatusBadge status={h.result === "success" ? "success" : "failed"} />}</td>
                  <td className="text-muted">{h.requested_by || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {rb && !running && (
          <div className="mt-4 pt-3 border-t border-border">
            {confirm !== "rollback" ? (
              <button type="button" className={BTN2} disabled={!canAct || busy} onClick={() => setConfirm("rollback")}>
                Restore v{rb.from_version}
              </button>
            ) : (
              <div className="border border-warn/40 rounded p-3 bg-warn/10 text-sm">
                <div className="font-medium mb-1">Go back to v{rb.from_version}?</div>
                <div className="text-xs text-muted mb-3">
                  {rb.migrated
                    ? `That update changed the database, so this restores the backup taken just before it (${new Date(rb.finished_at).toLocaleString()}). Anything changed in PyXie since then is lost.`
                    : "That update did not change the database, so only the program is switched back; no data is touched."}{" "}
                  The site is unavailable for about 1 to 2 minutes.
                </div>
                <button type="button" className={BTN} disabled={busy} onClick={async () => { if (await post("rollback")) { setAsked({ action: "rollback", version: rb.from_version, at: Date.now(), stateStart: data.state.started_at ?? null }); setConfirm(null); } }}>
                  {busy ? "Starting…" : `Restore v${rb.from_version}`}
                </button>
                <button type="button" className={`${BTN2} ml-2`} onClick={() => setConfirm(null)}>Cancel</button>
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
