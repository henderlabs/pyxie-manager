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
  no_credential: "No credential is saved for this PVE target (Credentials page). Use Open in PVE meanwhile.",
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

const MAX_TYPED_CHARS = 4000;

/** X11 keysym for one character (what VNC key events carry). */
function keysymFor(ch: string): number | null {
  if (ch === "\n" || ch === "\r") return 0xff0d;
  if (ch === "\t") return 0xff09;
  const cp = ch.codePointAt(0)!;
  if (cp < 0x20) return null;
  return cp < 0x100 ? cp : 0x01000000 + cp;
}

const SIZES: { key: string; label: string; height: string }[] = [
  { key: "small", label: "Small", height: "420px" },
  { key: "medium", label: "Medium", height: "640px" },
  { key: "large", label: "Large", height: "860px" },
  { key: "fill", label: "Fill window", height: "max(480px, calc(100vh - 300px))" },
];

const KEY_SHIFT_L = 0xffe1;
/** US layout: characters that need Shift held. QEMU's VNC server maps a keysym to the unshifted key
 * unless Shift is sent explicitly (what a real keyboard does), so we press it ourselves. */
const NEEDS_SHIFT = /[A-Z~!@#$%^&*()_+{}|:"<>?]/;

type State = "idle" | "connecting" | "connected" | "ended";

/** noVNC (bundled) talking to PyXie's ticketed websocket proxy. Mounted only while the Console
 * tab is open; leaving the tab disconnects. The 15 min idle limit is enforced here (VNC itself
 * never goes quiet on the wire); the 4 h cap and session checks are enforced server-side. */
export default function ConsoleViewer({ workloadId, info }: { workloadId: string; info: ConsoleInfo | null }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const screenRef = useRef<HTMLDivElement>(null);
  const [isFs, setIsFs] = useState(false);
  const rfbRef = useRef<any>(null);
  const idleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [state, setState] = useState<State>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [showClip, setShowClip] = useState(false);
  const [outText, setOutText] = useState("");
  const [fromGuest, setFromGuest] = useState("");
  const [clipNote, setClipNote] = useState<string | null>(null);
  const typingRef = useRef(false);
  const [size, setSize] = useState("fill");

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

  useEffect(() => {
    try {
      const saved = localStorage.getItem("pyxie:console-size");
      if (saved && SIZES.some((z) => z.key === saved)) setSize(saved);
    } catch { /* storage unavailable */ }
  }, []);

  useEffect(() => {
    const onChange = () => setIsFs(document.fullscreenElement === wrapRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.();
  }

  function pickSize(key: string) {
    setSize(key);
    try { localStorage.setItem("pyxie:console-size", key); } catch { /* ignore */ }
  }

  useEffect(() => () => {
    if (idleRef.current) clearTimeout(idleRef.current);
    try { rfbRef.current?.disconnect(); } catch { /* ignore */ }
  }, []);

  function sendToGuestClipboard() {
    if (!rfbRef.current || !outText) return;
    rfbRef.current.clipboardPasteFrom(outText);
    bumpIdle();
    setClipNote("Sent to the guest's clipboard. Press Ctrl+V inside the guest. This only works if the guest runs a clipboard agent (spice-vdagent); otherwise use Type it in.");
  }

  async function typeIntoGuest() {
    const rfb = rfbRef.current;
    if (!rfb || !outText || typingRef.current) return;
    const chars = Array.from(outText.slice(0, MAX_TYPED_CHARS));
    typingRef.current = true;
    setClipNote(`Typing ${chars.length} characters…`);
    rfb.focus();
    for (const ch of chars) {
      if (!rfbRef.current) break;
      const sym = keysymFor(ch);
      if (sym === null) continue;
      const shift = NEEDS_SHIFT.test(ch);
      if (shift) rfb.sendKey(KEY_SHIFT_L, "ShiftLeft", true);
      rfb.sendKey(sym, null, true);
      rfb.sendKey(sym, null, false);
      if (shift) rfb.sendKey(KEY_SHIFT_L, "ShiftLeft", false);
      await new Promise((r) => setTimeout(r, 4));
    }
    typingRef.current = false;
    bumpIdle();
    setClipNote(outText.length > MAX_TYPED_CHARS ? `Typed the first ${MAX_TYPED_CHARS} characters.` : "Typed.");
  }

  async function copyFromGuest() {
    try {
      await navigator.clipboard.writeText(fromGuest);
      setClipNote("Copied to your clipboard.");
    } catch {
      setClipNote("Your browser blocked clipboard access. Select the text above and copy it.");
    }
  }

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
      rfb.addEventListener("clipboard", (e: any) => {
        const text = e?.detail?.text ?? "";
        setFromGuest(text);
        setShowClip(true);
        navigator.clipboard?.writeText(text).catch(() => {});
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
    <div ref={wrapRef} className={isFs ? "space-y-2 bg-canvas p-3 flex flex-col h-screen overflow-auto" : "space-y-2"}>
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
            <button type="button" onClick={() => setShowClip((v) => !v)} aria-expanded={showClip} className="px-2.5 py-1 rounded border border-border hover:bg-surface2">Clipboard {showClip ? "▾" : "▸"}</button>
            <span className="inline-flex rounded border border-border overflow-hidden" role="group" aria-label="Console size">
              {SIZES.map((z) => (
                <button key={z.key} type="button" onClick={() => pickSize(z.key)}
                  className={`px-2 py-1 text-xs border-r border-border last:border-r-0 ${size === z.key ? "bg-surface2 text-text" : "text-muted hover:text-text"}`}>
                  {z.label}
                </button>
              ))}
            </span>
            <button type="button" onClick={toggleFullscreen} className="px-2.5 py-1 rounded border border-border hover:bg-surface2">{isFs ? "Exit full screen" : "Full screen"}</button>
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
      {live && showClip && (
        <div className="rounded border border-border px-2 py-1.5 text-sm">
          <div className="flex items-stretch gap-2">
            <textarea
              value={outText}
              onChange={(e) => setOutText(e.target.value)}
              rows={1}
              aria-label="Text to send to the guest"
              placeholder="Paste or type text for the guest"
              className="input flex-1 min-w-0 font-mono text-xs resize-y max-h-32"
            />
            <button type="button" disabled={state !== "connected" || !outText} onClick={typeIntoGuest} className="px-2.5 py-1 rounded border border-border hover:bg-surface2 disabled:opacity-50 whitespace-nowrap">Type it in</button>
            <button type="button" disabled={state !== "connected" || !outText} onClick={sendToGuestClipboard} className="px-2.5 py-1 rounded border border-border hover:bg-surface2 disabled:opacity-50 whitespace-nowrap">Send to guest clipboard</button>
            <button type="button" onClick={() => setShowClip(false)} aria-label="Hide clipboard" className="px-2 py-1 rounded border border-border text-muted hover:text-text">✕</button>
          </div>
          {fromGuest && (
            <div className="flex items-stretch gap-2 mt-1.5">
              <textarea readOnly value={fromGuest} rows={1} aria-label="Text copied in the guest" className="input flex-1 min-w-0 font-mono text-xs resize-y max-h-32" />
              <button type="button" onClick={copyFromGuest} className="px-2.5 py-1 rounded border border-border hover:bg-surface2 whitespace-nowrap">Copy to my clipboard</button>
            </div>
          )}
          <p className="text-[11px] text-muted mt-1">
            {clipNote ?? `Type it in sends keystrokes and works on any guest (up to ${MAX_TYPED_CHARS} characters). Send to guest clipboard and copy-back need a clipboard agent in the guest. Nothing is stored or logged.`}
          </p>
        </div>
      )}
      <div
        ref={screenRef}
        onKeyDownCapture={bumpIdle}
        onMouseDownCapture={bumpIdle}
        onMouseMoveCapture={() => { if (state === "connected") bumpIdle(); }}
        style={live ? (isFs ? { flex: "1 1 0", minHeight: 240 } : { height: SIZES.find((z) => z.key === size)?.height, minHeight: 240, minWidth: 320, maxWidth: "100%", resize: "both" }) : undefined}
        className={live ? "w-full bg-black rounded border border-border overflow-hidden" : "hidden"}
      />
      <p className={isFs ? "hidden" : "text-[11px] text-muted"}>
        Drag the bottom-right corner to resize freely. The console shows the guest's own screen with full keyboard and mouse control. Opening and closing it is recorded in the audit log.
      </p>
    </div>
  );
}
