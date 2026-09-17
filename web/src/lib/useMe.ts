"use client";

import { useEffect, useState } from "react";
import type { Me } from "@/lib/api";

// Shared client-side "who am I" fetch so write-surface components can hide
// their own buttons for Viewer accounts without each re-implementing the
// fetch. This is UI polish only -- the real boundary is the backend's
// require_admin 403 on every mutating endpoint; a Viewer who reaches a
// write action some other way (stale cache, a route this hook hasn't been
// added to yet) still gets rejected server-side, not granted access.
//
// Deduplicated/cached at module scope: 32 different components call this
// hook, several of them once PER ROW (Power/Migrate/Resize/notes on the
// Workloads table). Without this, a 130-VM page like cre-pyxie's fired
// 600-900+ *identical* concurrent GET /api/auth/me requests -- a real
// request storm found live, the actual cause of "slow page loads," not
// anything about the page's own data. A short TTL keeps it re-fetching
// occasionally (e.g. after a login-as-a-different-user in the same tab,
// which shares cookies across every open tab) rather than caching forever.
const CACHE_TTL_MS = 30_000;

let cachedMe: Me | null | undefined = undefined;
let cachedAt = 0;
let inFlight: Promise<Me | null> | null = null;
const subscribers = new Set<(me: Me | null | undefined) => void>();

function fetchMe(): Promise<Me | null> {
  if (!inFlight) {
    inFlight = fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => {
        cachedMe = me;
        cachedAt = Date.now();
        inFlight = null;
        subscribers.forEach((cb) => cb(me));
        return me;
      })
      .catch(() => {
        cachedMe = null;
        cachedAt = Date.now();
        inFlight = null;
        subscribers.forEach((cb) => cb(null));
        return null;
      });
  }
  return inFlight;
}

export function useMe(): Me | null | undefined {
  const [me, setMe] = useState<Me | null | undefined>(cachedMe);

  useEffect(() => {
    subscribers.add(setMe);
    const stale = cachedMe === undefined || Date.now() - cachedAt > CACHE_TTL_MS;
    if (stale) {
      fetchMe();
    } else {
      setMe(cachedMe);
    }
    return () => {
      subscribers.delete(setMe);
    };
  }, []);

  return me;
}
