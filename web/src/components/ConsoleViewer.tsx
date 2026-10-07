"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ConsoleInfo = {
  kind: string;
  vmid: number;
  node: string;
  status: string;
  pve_url: string;
  embedded: "ready" | "admin_only" | "disabled" | "no_credential" | "not_running" | "unavailable";
};

const IDLE_LIMIT_MS = 15 * 60 * 1000;

const EMBEDDED_HINT: Record<ConsoleInfo["embedded"], string> = {
  ready: "",
  admin_only: "The embedded console is for admin accounts. Use Open in PVE below.",
  disabled: "The embedded console is switched off. An admin can turn it on in Settings.",
  no_credential: "No 'console' credential is saved yet (Credentials page). Use Open in PVE meanwhile.",
  not_running: "The console is available while the guest is running.",
  unavailable: "This guest is not currently visible to PVE.",
};

export function useConsoleInfo(workloadId: string) {
  const [info, setInfo] = useState<ConsoleInfo | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/api/workloads/${workloadId}/console-info`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setInfo(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [workloadId]);
  return info;
}

type State = "idle" | "connecting" | "connected" | "ended";

/** noVNC (bundled) talking to PyXie's ticketed websocket proxy. Mounted only while the Console
 * tab is open; leaving the tab disconnects. The 15 min idle limit is enforced here (VNC itself
 * never goes quiet on the wire); the 4 h cap and session checks are enforced server-side. */
export default function ConsoleViewer({ workloadId, info }: { workloadId: string; info: ConsoleInfo | null }) {
  const screenRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<any>(null);
  const idleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [state, setState] = useState<State>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const disconnect = useCallback((why?: string) => {
    if (idleRef.current) clearTimeout(idleRef.current);
    try { rfbRef.current?.disconnect(); } catch { /* already closed */ }
    rfbRef.current = null;
    setState("ended");
    if (why) setMessage(why);
  }, []);

  const bumpIdle = useCallback(() => {
    if (idleRef.current) clearTimeout(idleRef.current);
    idleRef.current = setTimeout(() => disconnect("Disconnected after 15 minutes without input."), IDLE_LIMIT_MS);
  }, [disconnect]);

  useEffect(() => () => {
    if (idleRef.current) clearTimeout(idleRef.current);
    try { rfbRef.current?.disconnect(); } catch { /* ignore */ }
  }, []);

  async function connect() {
    setState("connecting");
    setMessage(null);
    try {
      const res = await fetch(`/api/workloads/${workloadId}/console`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      const mod: any = await import("@novnc/novnc/lib/rfb");
      const RFB = mod.default ?? mod;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const rfb = new RFB(screenRef.current!, `${proto}://${window.location.host}${data.ws_path}`, {
        credentials: { password: data.password },
        wsProtocols: ["binary"],
      });
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.addEventListener("connect", () => { setState("connected"); bumpIdle(); rfb.focus(); });
      rfb.addEventListener("disconnect", (e: any) => {
        if (idleRef.current) clearTimeout(idleRef.current);
        rfbRef.current = null;
        setState("ended");
        setMessage((m) => m ?? (e?.detail?.clean ? "Console closed." : "The console connection ended or could not be opened."));
      });
      rfb.addEventListener("credentialsrequired", () => rfb.sendCredentials({ password: data.password }));
      rfbRef.current = rfb;
    } catch (e) {
      setState("ended");
      setMessage((e as Error).message);
    }
  }

  const live = state === "connecting" || state === "connected";
  const canEmbed = info?.embedded === "ready";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap text-sm">
        {!live && (
          <button type="button" onClick={connect} disabled={!canEmbed}
            className="px-2.5 py-1 rounded border border-border text-sm text-text hover:bg-surface2 disabled:opacity-50 disabled:cursor-not-allowed">
            {state === "ended" ? "Reconnect" : "Connect"}
          </button>
        )}
        {live && (
          <>
            <button type="button" onClick={() => disconnect("Console closed.")} className="px-2.5 py-1 rounded border border-border hover:bg-surface2">Disconnect</button>
            <button type="button" disabled={state !== "connected"} onClick={() => rfbRef.current?.sendCtrlAltDel()} className="px-2.5 py-1 rounded border border-border hover:bg-surface2 disabled:opacity-50">Ctrl-Alt-Del</button>
            <button type="button" onClick={() => screenRef.current?.requestFullscreen?.()} className="px-2.5 py-1 rounded border border-border hover:bg-surface2">Full screen</button>
          </>
        )}
        {info && (
          <a href={info.pve_url} target="_blank" rel="noopener noreferrer" className="px-2.5 py-1 rounded border border-border text-muted hover:text-text">
            Open in PVE
          </a>
        )}
        {state === "connecting" && <span className="text-muted">Connecting…</span>}
        {state === "connected" && <span className="text-muted">Connected. Idle limit 15 min.</span>}
      </div>
      {!canEmbed && info && EMBEDDED_HINT[info.embedded] && <p className="text-xs text-muted">{EMBEDDED_HINT[info.embedded]}</p>}
      {message && <p className="text-xs text-bad">{message}</p>}
      <div
        ref={screenRef}
        onKeyDownCapture={bumpIdle}
        onMouseDownCapture={bumpIdle}
        onMouseMoveCapture={() => { if (state === "connected") bumpIdle(); }}
        className={live ? "w-full h-[520px] bg-black rounded border border-border overflow-hidden" : "hidden"}
      />
      <p className="text-[11px] text-muted">
        The console shows the guest's own screen with full keyboard and mouse control. Opening and closing it is recorded in the audit log.
      </p>
    </div>
  );
}
