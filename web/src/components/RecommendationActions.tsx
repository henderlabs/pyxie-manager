"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

/** Acknowledge / Dismiss for one recommendation, state-aware: open -> Acknowledge, Dismiss; acknowledged -> Dismiss,
 * Un-acknowledge; dismissed -> Restore. Admins only. Same behaviour as Health findings. */
export default function RecommendationActions({ id, state = "open" }: { id: string; state?: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function act(action: "acknowledge" | "dismiss" | "reopen") {
    setPending(true);
    try {
      await fetch("/api/recommendations/triage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: [id], action }),
      });
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  const btn = "px-2 py-1 rounded bg-surface2 border border-border hover:bg-surface2/70";
  const dismissed = state === "dismissed" || state === "snoozed";
  return (
    <div className="flex gap-1.5 text-xs">
      {state === "open" && (
        <>
          <button disabled={pending} onClick={() => act("acknowledge")} className={`${btn} text-text`}>Acknowledge</button>
          <button disabled={pending} onClick={() => act("dismiss")} title="Hides this suggestion until it changes or clears and comes back" className={`${btn} text-muted`}>Dismiss</button>
        </>
      )}
      {state === "acknowledged" && (
        <>
          <button disabled={pending} onClick={() => act("dismiss")} className={`${btn} text-muted`}>Dismiss</button>
          <button disabled={pending} onClick={() => act("reopen")} className={`${btn} text-text`}>Un-acknowledge</button>
        </>
      )}
      {dismissed && <button disabled={pending} onClick={() => act("reopen")} className={`${btn} text-text`}>Restore</button>}
    </div>
  );
}
