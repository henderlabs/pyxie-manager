"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useMe } from "@/lib/useMe";
import type { UpdatesResponse } from "@/lib/updates";

const KEY = "pyxie:update-dismissed";

/** A slim bar at the top of every page, for administrators only, when a newer release is waiting
 * (or an update is running). Dismissing hides it for that version only. */
export default function UpdateBanner() {
  const me = useMe();
  const isAdmin = me?.is_admin === true;
  const [info, setInfo] = useState<{ latest?: string; available: boolean; running: boolean; instance?: string; current: string } | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);

  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(KEY));
    } catch {
      // private window: nothing remembered
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    const load = () =>
      fetch("/api/system/updates", { cache: "no-store" })
        .then((r) => (r.ok ? (r.json() as Promise<UpdatesResponse>) : null))
        .then((d) => {
          if (!d || cancelled) return;
          setInfo({ latest: d.status.latest, available: !!d.status.update_available, running: d.state.state === "running", instance: d.status.instance, current: d.current });
        })
        .catch(() => {});
    load();
    const iv = setInterval(() => {
      if (!document.hidden) load();
    }, 300000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [isAdmin]);

  if (!isAdmin || !info) return null;
  if (info.running) {
    return (
      <div className="mb-4 px-3 py-2 rounded border border-warn/40 bg-warn/10 text-sm text-warn">
        An update is in progress. <Link href="/platform/settings/updates" className="underline">Watch it</Link>
      </div>
    );
  }
  if (!info.available || dismissed === info.latest) return null;
  return (
    <div className="mb-4 px-3 py-2 rounded border border-accent/40 bg-accent/10 text-sm flex items-center justify-between gap-3">
      <div>
        <span className="text-text">PyXie <b>v{info.latest}</b> is available for {info.instance || "this server"} (running v{info.current}).</span>{" "}
        <Link href="/platform/settings/updates" className="text-accent underline">Review and update</Link>
      </div>
      <button
        type="button"
        className="text-muted hover:text-text text-xs shrink-0"
        onClick={() => {
          setDismissed(info.latest || null);
          try {
            localStorage.setItem(KEY, info.latest || "");
          } catch {
            // not remembered
          }
        }}
      >
        Dismiss for this version ✕
      </button>
    </div>
  );
}
