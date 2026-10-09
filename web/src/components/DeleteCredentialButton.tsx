"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMe } from "@/lib/useMe";

// Removes a credential purpose (and its stored secret). Two-step: the first click asks, the second deletes.
export default function DeleteCredentialButton({ targetId, credentialId, slotName }: { targetId: string; credentialId: string; slotName: string }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;
  if (slotName === "inventory") return null; // discovery runs on it; replace it with Edit instead

  async function remove() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/pve-targets/${targetId}/credentials/${credentialId}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (!confirming) {
    return (
      <button onClick={() => setConfirming(true)} className="text-xs text-bad hover:underline">
        Delete
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-2 text-xs">
      <span className="text-muted">Delete the “{slotName}” credential and its stored secret? This does not touch Proxmox.</span>
      <button onClick={remove} disabled={pending} className="px-2 py-1 rounded font-medium bg-bad/10 text-bad border border-bad disabled:opacity-50">
        {pending ? "Deleting…" : "Yes, delete"}
      </button>
      <button onClick={() => setConfirming(false)} className="px-2 py-1 rounded bg-surface text-muted border border-border">
        Cancel
      </button>
      {error && <span className="text-bad">{error}</span>}
    </span>
  );
}
