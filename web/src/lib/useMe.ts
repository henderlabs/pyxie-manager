"use client";

import { useEffect, useState } from "react";
import type { Me } from "@/lib/api";

// Shared client-side "who am I" fetch so write-surface components can hide
// their own buttons for Viewer accounts without each re-implementing the
// fetch. This is UI polish only -- the real boundary is the backend's
// require_admin 403 on every mutating endpoint; a Viewer who reaches a
// write action some other way (stale cache, a route this hook hasn't been
// added to yet) still gets rejected server-side, not granted access.
export function useMe(): Me | null | undefined {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  return me;
}
