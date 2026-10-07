"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import ThemeToggle from "@/components/ThemeToggle";
import { useMe } from "@/lib/useMe";
import type { UpdatesResponse } from "@/lib/updates";

type UpdateInfo = { latest?: string; available: boolean; running: boolean; instance?: string; current: string };

/** Administrators only: is a newer release waiting, or an update running? Refreshed on every page change, on window focus, and every minute. */
function useUpdateInfo(enabled: boolean, pathname: string): UpdateInfo | null {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  useEffect(() => {
    if (!enabled) return;
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
    // The top bar stays mounted while you move between pages, so refresh on navigation and when the window regains
    // focus, not only on a timer: a release found by "Check now" must show up at once.
    const iv = setInterval(() => {
      if (!document.hidden) load();
    }, 60000);
    const onVisible = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      cancelled = true;
      clearInterval(iv);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [enabled, pathname]);
  return info;
}

/** Top right of every page, on the same row as the page title: an update pill when one is waiting,
 * then one capsule holding which PyXie this is, who is signed in (with Sign out) and the theme switch.
 * Floats over the page (no row of its own), so nothing is pushed down when the update pill appears. */
export default function TopBar({ instance }: { instance: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useMe();
  const info = useUpdateInfo(me?.is_admin === true, pathname);
  if (pathname === "/login" || pathname.startsWith("/accept-invite")) return null;

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  const name = me ? me.display_name || me.email : "";
  return (
    <div className="absolute top-[22px] right-6 z-20 flex items-center gap-2 text-xs">
      {info?.running ? (
        <Link href="/platform/settings/updates" className="inline-flex items-center gap-1.5 h-8 rounded-lg border border-warn/40 bg-warn/10 text-warn px-2.5 font-medium hover:bg-warn/20" title="An update is in progress. Click to watch it.">
          <span className="w-1.5 h-1.5 rounded-full bg-warn animate-pulse" />
          Updating…
        </Link>
      ) : info?.available ? (
        <Link
          href="/platform/settings/updates"
          className="inline-flex items-center gap-1.5 h-8 rounded-lg border border-accent/40 bg-accent/10 text-accent px-2.5 font-medium hover:bg-accent/20"
          title={`PyXie v${info.latest} is available for ${info.instance || "this server"} (running v${info.current}). Click to review and update.`}
        >
          <span className="relative flex h-1.5 w-1.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
          </span>
          Update to v{info.latest}
        </Link>
      ) : null}
      <div className="flex items-stretch h-8 rounded-lg border border-border bg-surface divide-x divide-border overflow-hidden shadow-sm">
        <span className="flex items-center px-3 font-semibold tracking-wide text-text" title="Which PyXie this is">
          {instance}
        </span>
        {me ? (
          <>
            <Link href="/profile" className="flex items-center gap-1.5 px-3 text-muted hover:text-text" title={`${me.email} — edit profile`}>
              <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-accent/15 text-accent text-[10px] font-semibold uppercase">{name.charAt(0)}</span>
              {name}
            </Link>
            <button type="button" onClick={logout} className="flex items-center px-3 text-muted hover:text-accent hover:bg-surface2">
              Sign out
            </button>
          </>
        ) : (
          <span className="flex items-center px-3 text-muted">Read-only</span>
        )}
        <div className="flex items-center px-1.5">
          <ThemeToggle bare />
        </div>
      </div>
    </div>
  );
}
