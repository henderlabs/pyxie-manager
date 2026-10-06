"use client";

import { useEffect, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";

/** Slim bar at the top of the window (and a busy cursor) from the moment an internal link is
 * clicked until the next page has arrived, so a slow page never looks like a missed click. */
export default function NavProgress() {
  const pathname = usePathname();
  const search = useSearchParams();
  const [busy, setBusy] = useState(false);
  const here = `${pathname}?${search.toString()}`;

  // The URL changed: the new page has arrived.
  useEffect(() => {
    setBusy(false);
  }, [here]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || (a.target && a.target !== "_self") || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      setBusy(true);
    }
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, []);

  // Never leave the bar up forever if a navigation fails or is cancelled.
  useEffect(() => {
    if (!busy) return;
    const t = setTimeout(() => setBusy(false), 20000);
    return () => clearTimeout(t);
  }, [busy]);

  useEffect(() => {
    document.documentElement.classList.toggle("pyxie-navigating", busy);
    return () => document.documentElement.classList.remove("pyxie-navigating");
  }, [busy]);

  if (!busy) return null;
  return (
    <div className="fixed top-0 left-0 right-0 h-0.5 z-50 overflow-hidden pointer-events-none" role="progressbar" aria-label="Loading page">
      <div className="pyxie-nav-bar h-full w-1/3 bg-accent" />
    </div>
  );
}
