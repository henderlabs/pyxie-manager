"use client";

import { useEffect, useState } from "react";
import type { HostMaintenanceCredentialRecord, HostMaintenanceStatus, Node } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function HostMaintenanceSetup({
  targetId,
  nodes,
  initialCredential,
}: {
  targetId: string;
  nodes: Node[];
  initialCredential: HostMaintenanceCredentialRecord | null;
}) {
  const [credential, setCredential] = useState(initialCredential);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, HostMaintenanceStatus | null>>({});
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  function refreshNodeStatuses() {
    nodes.forEach((n) => {
      fetch(`/api/nodes/${n.id}/host-maintenance-status`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => setNodeStatuses((s) => ({ ...s, [n.id]: d })))
        .catch(() => {});
    });
  }

  useEffect(() => {
    if (credential) refreshNodeStatuses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [credential?.id]);

  async function generate(force: boolean) {
    setGenerating(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/pve-targets/${targetId}/host-maintenance-credential/generate${force ? "?force=true" : ""}`,
        { method: "POST" }
      );
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Generate failed");
        return;
      }
      setCredential(data);
    } finally {
      setGenerating(false);
    }
  }

  function downloadKit() {
    window.location.href = `/api/pve-targets/${targetId}/host-maintenance-kit`;
  }

  function downloadUninstallScript() {
    window.location.href = `/api/pve-targets/${targetId}/host-maintenance-uninstall-script`;
  }

  return (
    <div className="text-sm">
      {!credential ? (
        isAdmin ? (
          <div>
            <p className="text-muted mb-2">
              No host-maintenance credential yet for this target -- generate one to start connecting nodes for real
              package patching.
            </p>
            <button
              onClick={() => generate(false)}
              disabled={generating}
              className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
            >
              {generating ? "Generating…" : "Generate Keypair"}
            </button>
          </div>
        ) : (
          <p className="text-muted">No host-maintenance credential yet for this target.</p>
        )
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="text-xs text-muted">
              SSH identity: <span className="text-text font-mono">{credential.ssh_username}</span> · created{" "}
              {new Date(credential.created_at).toLocaleString()}
            </div>
            {isAdmin && (
              <div className="flex gap-2">
                <button
                  onClick={downloadKit}
                  className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10"
                >
                  Download Provisioning Kit
                </button>
                <button
                  onClick={() => generate(true)}
                  disabled={generating}
                  className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50"
                >
                  {generating ? "Regenerating…" : "Regenerate Keypair"}
                </button>
                <button
                  onClick={downloadUninstallScript}
                  className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-bad hover:bg-bad/10"
                >
                  Download Uninstall Script
                </button>
              </div>
            )}
          </div>
          <p className="text-xs text-muted">
            On each node below: unpack the kit and run{" "}
            <code className="text-text">sudo ./install.sh</code>, then come back here to probe and pin its host key.
            To fully remove a node later, run <code className="text-text">sudo ./uninstall.sh</code> there and click
            Disconnect below.
          </p>

          <div className="border border-border rounded divide-y divide-border">
            {nodes.length === 0 && <div className="px-3 py-2 text-xs text-muted italic">No nodes discovered for this cluster yet.</div>}
            {nodes.map((n) => (
              <NodeReadinessRow key={n.id} node={n} status={nodeStatuses[n.id]} onChanged={refreshNodeStatuses} isAdmin={isAdmin} />
            ))}
          </div>
        </div>
      )}
      {error && <div className="text-sm text-bad mt-2">{error}</div>}
    </div>
  );
}

function NodeReadinessRow({
  node,
  status,
  onChanged,
  isAdmin,
}: {
  node: Node;
  status: HostMaintenanceStatus | null | undefined;
  onChanged: () => void;
  isAdmin: boolean;
}) {
  const [probing, setProbing] = useState(false);
  const [pinning, setPinning] = useState(false);
  const [probeResult, setProbeResult] = useState<{ key_type: string; key_base64: string; fingerprint: string } | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  async function probe() {
    setProbing(true);
    setProbeError(null);
    setProbeResult(null);
    try {
      const res = await fetch(`/api/nodes/${node.id}/ssh-host-key/probe`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setProbeError(data.error || "Probe failed");
        return;
      }
      setProbeResult(data);
    } finally {
      setProbing(false);
    }
  }

  async function pin() {
    if (!probeResult) return;
    setPinning(true);
    try {
      await fetch(`/api/nodes/${node.id}/ssh-host-key`, {
        method: "PUT",
        body: JSON.stringify({ key_type: probeResult.key_type, key_base64: probeResult.key_base64 }),
      });
      setProbeResult(null);
      onChanged();
    } finally {
      setPinning(false);
    }
  }

  async function disconnect() {
    setDisconnecting(true);
    try {
      await fetch(`/api/nodes/${node.id}/ssh-host-key`, { method: "DELETE" });
      setConfirmingDisconnect(false);
      onChanged();
    } finally {
      setDisconnecting(false);
    }
  }

  const pinned = !!status?.host_key_pinned;
  const reachable = !!status?.reachable;

  return (
    <div className="px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="text-text font-medium flex items-center gap-2">
          {node.name}
          {node.maintenance_mode && (
            <span
              className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-warn/20 text-warn uppercase tracking-wide"
              title="In maintenance mode"
            >
              Maint
            </span>
          )}
        </span>
        {!pinned ? (
          <span className="text-[11px] font-semibold uppercase tracking-wide text-warn">Key not pinned</span>
        ) : reachable ? (
          <span className="text-[11px] font-semibold uppercase tracking-wide text-good">Ready</span>
        ) : (
          <span className="text-[11px] font-semibold uppercase tracking-wide text-bad">Unreachable</span>
        )}
      </div>

      {!pinned && !probeResult && isAdmin && (
        <button
          onClick={probe}
          disabled={probing}
          className="mt-1.5 px-2 py-1 rounded text-[11px] font-medium bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
        >
          {probing ? "Probing…" : "Probe Host Key"}
        </button>
      )}
      {probeError && <div className="text-[11px] text-bad mt-1">{probeError}</div>}
      {probeResult && isAdmin && (
        <div className="mt-1.5 text-[11px] bg-surface2/60 border border-border rounded p-2">
          <div className="text-muted">
            Fingerprint — compare against the node&apos;s own console (
            <code className="text-text">ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code>) before pinning:
          </div>
          <div className="text-text font-mono mt-0.5">{probeResult.fingerprint}</div>
          <div className="flex gap-2 mt-1.5">
            <button
              onClick={pin}
              disabled={pinning}
              className="px-2 py-1 rounded text-[11px] font-medium bg-ink text-on-ink border border-good hover:bg-good/10 disabled:opacity-50"
            >
              {pinning ? "Pinning…" : "Confirms match — Pin"}
            </button>
            <button onClick={() => setProbeResult(null)} className="text-[11px] text-muted hover:text-text">
              cancel
            </button>
          </div>
        </div>
      )}

      {pinned && reachable && (
        <div className="text-[11px] text-muted mt-1">
          wrapper {status?.wrapper_version}
          {status?.wrapper_outdated ? <span className="text-warn"> (update available: {status?.kit_wrapper_version}; Setup guide &gt; Script builder: Host wrapper)</span> : null}
          {" "}· contract {status?.contract_version} · {status?.upgradable_count ?? "?"}{" "}
          packages pending
          {status?.reboot_required ? <span className="text-warn"> · reboot required</span> : null}
        </div>
      )}
      {pinned && !reachable && (
        <div className="text-[11px] text-bad mt-1">
          {status?.error || "not reachable"} — if <code>install.sh</code> hasn&apos;t been run on this node yet, that&apos;s
          almost certainly why.
        </div>
      )}

      {pinned && !confirmingDisconnect && isAdmin && (
        <button
          onClick={() => setConfirmingDisconnect(true)}
          className="mt-1.5 px-2 py-1 rounded text-[11px] font-medium bg-ink text-on-ink border border-bad hover:bg-bad/10"
        >
          Disconnect
        </button>
      )}
      {confirmingDisconnect && isAdmin && (
        <div className="mt-1.5 text-[11px] bg-surface2/60 border border-border rounded p-2">
          <div className="text-muted">
            This unpins the host key — PyXie stops managing this node until it&apos;s re-probed and re-pinned. It does
            NOT remove anything from the node itself; run <code className="text-text">sudo ./uninstall.sh</code>{" "}
            there separately if you want that too.
          </div>
          <div className="flex gap-2 mt-1.5">
            <button
              onClick={disconnect}
              disabled={disconnecting}
              className="px-2 py-1 rounded text-[11px] font-medium bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-50"
            >
              {disconnecting ? "Disconnecting…" : "Confirm Disconnect"}
            </button>
            <button onClick={() => setConfirmingDisconnect(false)} className="text-[11px] text-muted hover:text-text">
              cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
