"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

export default function RecommendationActions({ id }: { id: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function setState(state: string, snoozeDays?: number) {
    setPending(true);
    try {
      await fetch(`/api/recommendations/${id}/lifecycle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lifecycle_state: state, snooze_days: snoozeDays }),
      });
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex gap-1.5 text-xs">
      <button disabled={pending} onClick={() => setState("acknowledged")} className="px-2 py-1 rounded bg-surface2 border border-border text-text hover:bg-surface2/70">
        Acknowledge
      </button>
      <button
        disabled={pending}
        onClick={() => setState("snoozed", 30)}
        title="Hides this suggestion for 30 days -- reappears then if the underlying condition is still true"
        className="px-2 py-1 rounded bg-surface2 border border-border text-muted hover:bg-surface2/70"
      >
        Dismiss
      </button>
    </div>
  );
}
