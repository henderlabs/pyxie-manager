"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

// Every usage of this component fires a POST -- it's the generic
// write-trigger button used across Integrations, Protection, and
// NodeActionsForm. Gating it here once covers all of them rather than
// gating each call site separately.
export default function ActionButton({
  href,
  label,
  pendingLabel,
  variant = "secondary",
}: {
  href: string;
  label: string;
  pendingLabel?: string;
  variant?: "primary" | "secondary";
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function run() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(href, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
      } else {
        router.refresh();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  const cls =
    variant === "primary"
      ? "bg-ink text-white border border-accent hover:bg-accent/10"
      : "bg-surface2 text-text border border-border hover:bg-surface2/70";

  return (
    <div className="inline-flex flex-col items-start">
      <button
        onClick={run}
        disabled={pending}
        className={`px-3 py-1.5 rounded text-sm font-medium disabled:opacity-50 ${cls}`}
      >
        {pending ? pendingLabel || "Working…" : label}
      </button>
      {error && <span className="text-xs text-bad mt-1">{error}</span>}
    </div>
  );
}
