"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

export default function HoldDownSetting({ initial }: { initial: number }) {
  const router = useRouter();
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [minutes, setMinutes] = useState(String(initial));
  const [status, setStatus] = useState<string | null>(null);

  async function save() {
    setStatus(null);
    const n = Number(minutes);
    if (!Number.isInteger(n) || n < 0 || n > 1440) {
      setStatus("Enter a whole number of minutes from 0 to 1440.");
      return;
    }
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notification_hold_down_minutes: n }),
    });
    if (!res.ok) {
      setStatus(`Failed (${res.status})`);
      return;
    }
    setStatus("Saved.");
    router.refresh();
  }

  return (
    <div className="mb-4 pb-4 border-b border-border">
      <div className="flex items-center gap-2 text-sm text-text">
        Wait
        <input
          type="number"
          min={0}
          max={1440}
          disabled={!isAdmin}
          value={minutes}
          onChange={(e) => setMinutes(e.target.value)}
          className="w-20 bg-[rgb(var(--c-canvas))] border border-[rgb(var(--c-border))] rounded px-2 py-1 text-sm text-[rgb(var(--c-text))]"
        />
        minutes before announcing a change
        {isAdmin && (
          <button
            type="button"
            onClick={save}
            className="ml-2 px-3 py-1 rounded text-sm border border-border hover:bg-text/5"
          >
            Save
          </button>
        )}
        {status && <span className={`text-xs ${status === "Saved." ? "text-good" : "text-bad"}`}>{status}</span>}
      </div>
      <p className="text-[11px] text-muted mt-1">
        A problem (or its recovery) is only announced once it has lasted this long, so a host that keeps dropping in
        and out sends one message when it settles instead of one per flap. 0 announces immediately.
      </p>
    </div>
  );
}
