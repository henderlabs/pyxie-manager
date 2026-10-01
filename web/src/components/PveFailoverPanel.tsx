"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { PveEndpoints } from "@/lib/api";
import { useMe } from "@/lib/useMe";

/** Under a PVE target: the entry point the operator added by hand, the cluster
 * members discovery found automatically (in the order PyXie fails over through
 * them), and the controls that select which members may be used and which one
 * is preferred. Saves immediately; admin only. */
export default function PveFailoverPanel({ targetId, data }: { targetId: string; data: PveEndpoints }) {
  const router = useRouter();
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const members = data.members;
  const disabledIds = members.filter((m) => !m.enabled).map((m) => m.node_id);

  async function save(preferred: string | null, disabled: string[]) {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/pve-targets/${targetId}/endpoints`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferred_node_id: preferred, disabled_node_ids: disabled }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(d.error || "Save failed");
      } else {
        router.refresh();
      }
    } catch {
      setError("Save failed");
    } finally {
      setPending(false);
    }
  }

  function toggle(nodeId: string, enabled: boolean) {
    const next = enabled ? disabledIds.filter((i) => i !== nodeId) : [...disabledIds, nodeId];
    const preferred = !enabled && data.preferred_node_id === nodeId ? null : data.preferred_node_id;
    save(preferred, next);
  }

  const usable = members.filter((m) => m.enabled);

  return (
    <details className="mt-3 text-xs">
      <summary className="cursor-pointer select-none text-muted">
        Failover: 1 manually added entry, {members.length} auto-discovered member{members.length === 1 ? "" : "s"}
      </summary>

      <div className="mt-3 space-y-4">
        <section>
          <div className="uppercase tracking-wider text-muted mb-1">Manually added</div>
          <div>
            <span className="font-mono text-text">
              {data.manual.host}:{data.manual.port}
            </span>
            {data.manual.matches_node && <span className="text-muted"> (this is {data.manual.matches_node})</span>}
          </div>
          <p className="text-muted mt-1 normal-case">
            The connection you entered. It is how PyXie first finds the cluster, and the last resort if none of the
            discovered members below can be reached.
          </p>
        </section>

        <section>
          <div className="uppercase tracking-wider text-muted mb-1">Auto-discovered cluster members</div>
          <p className="text-muted normal-case">
            Found automatically from the cluster. PyXie connects to the first reachable member in this order and moves
            down the list when one cannot be connected to, returning to the top once that member recovers.
          </p>

          <label className="flex items-center gap-2 mt-2">
            <span className="text-muted">Preferred member</span>
            <select
              className="bg-surface text-text border border-border rounded px-2 py-1"
              disabled={!isAdmin || pending}
              value={data.preferred_node_id ?? ""}
              onChange={(e) => save(e.target.value || null, disabledIds)}
            >
              <option value="">Automatic (the entry point above)</option>
              {usable.map((m) => (
                <option key={m.node_id} value={m.node_id}>
                  {m.node}
                </option>
              ))}
            </select>
          </label>

          <table className="mt-2 w-full text-left">
            <thead className="text-muted">
              <tr>
                <th className="font-normal pr-3 py-1">#</th>
                <th className="font-normal pr-3 py-1">Node</th>
                <th className="font-normal pr-3 py-1">Address</th>
                <th className="font-normal pr-3 py-1">Node status</th>
                <th className="font-normal pr-3 py-1">Use for failover</th>
                <th className="font-normal py-1">State</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.node_id} className={"border-t border-border" + (m.enabled ? "" : " opacity-60")}>
                  <td className="pr-3 py-1 text-muted">{m.order ?? "—"}</td>
                  <td className="pr-3 py-1 text-text">
                    {m.node}
                    {m.preferred && <span className="text-good"> · preferred</span>}
                  </td>
                  <td className="pr-3 py-1 font-mono">
                    {m.host}
                    {m.addressing === "dns" ? <span className="text-muted"> ({m.ip})</span> : <span className="text-muted"> (IP)</span>}
                  </td>
                  <td className="pr-3 py-1">{m.node_status ?? "—"}</td>
                  <td className="pr-3 py-1">
                    <input
                      type="checkbox"
                      checked={m.enabled}
                      disabled={!isAdmin || pending}
                      onChange={(e) => toggle(m.node_id, e.target.checked)}
                    />
                  </td>
                  <td className="py-1">
                    {m.state === "active" && <span className="text-good">Active</span>}
                    {m.state === "unreachable" && <span className="text-bad">Unreachable</span>}
                    {m.state === "standby" && <span className="text-muted">Standby</span>}
                    {m.state === "excluded" && <span className="text-muted">Excluded</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {error && <div className="text-bad mt-2">{error}</div>}
          {!isAdmin && <div className="text-muted mt-2">Only administrators can change failover selection.</div>}
        </section>
      </div>
    </details>
  );
}
