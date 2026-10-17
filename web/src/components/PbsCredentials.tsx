"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import PbsSshConnect from "@/components/PbsSshConnect";
import { PbsAccountsBuilder, PbsHostWrapperBuilder } from "@/components/HostSetupBuilder";
import PbsTargetForm from "@/components/PbsTargetForm";
import PbsTokenSlot, { type PbsToken } from "@/components/PbsTokenSlot";

type Server = { id: string; name: string; hostname: string; credentials: Record<string, PbsToken>; ssh: Record<string, unknown>; status: { updates?: { count: number } | null } | null; pending?: number | null };

/** Every Proxmox Backup Server with its API tokens (read-only and admin) and SSH access state. The same widgets appear on the server's own Access tab. */
export default function PbsCredentials({ tlsMode = "" }: { tlsMode?: string }) {
  const [servers, setServers] = useState<Server[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  useEffect(() => { fetch("/api/sites").then((r) => r.json()).then((d) => setSiteId(Array.isArray(d) && d[0] ? d[0].id : null)).catch(() => {}); }, []);
  const load = useCallback(() => { fetch("/api/pbs/servers").then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error || "failed"); setServers(d.map((x: Server) => ({ ...x, pending: x.status?.updates?.count ?? null }))); }).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { load(); }, [load]);
  const builder = (
    <Card className="mb-4" id="pbs">
      <details>
        <summary className="cursor-pointer select-none text-sm font-medium text-text">Create the backup server accounts (script you run on the backup server)</summary>
        <div className="mt-3"><PbsAccountsBuilder /></div>
      </details>
    </Card>
  );
  if (err) return <Card><div className="text-bad text-sm">{err}</div></Card>;
  if (!servers) return <Card><div className="text-sm text-muted">Reading backup servers…</div></Card>;
  if (servers.length === 0) return <>{builder}<Card><div className="text-sm text-muted mb-2">No backup servers yet. Create the accounts with the script above, then add the server with its read-only token.</div>{siteId && <PbsTargetForm siteId={siteId} />}</Card></>;
  return (
    <>
      {builder}
      {servers.map((s) => (
        <Card key={s.id} className="mb-4">
          <CardTitle><span className="mr-2 px-1.5 py-0.5 rounded bg-accent/15 text-accent normal-case tracking-normal">PBS</span>{s.name} <span className="normal-case tracking-normal text-muted">{s.hostname}</span></CardTitle>
          <div className="divide-y divide-border">
            <PbsTokenSlot serverId={s.id} slot="inventory" token={s.credentials.inventory} onChanged={load} />
            <PbsTokenSlot serverId={s.id} slot="administrative" token={s.credentials.administrative} onChanged={load} />
            <PbsTokenSlot serverId={s.id} slot="restore" token={s.credentials.restore} onChanged={load} />
            <PbsTokenSlot serverId={s.id} slot="backup" token={s.credentials.backup} onChanged={load} />
          </div>
          <div className="py-2 text-sm border-t border-border">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-text font-medium">ssh (host patching)</div>
                <div className="text-xs text-muted">{(s.ssh as { generated?: boolean }).generated ? `${(s.ssh as { username?: string }).username ?? "pyxie-hostmaint"} · ${(s.ssh as { host_key_pinned?: boolean }).host_key_pinned ? "host key pinned" : "host key not pinned"}` : "no key generated yet"}</div>
              </div>
              <div className="text-xs text-muted font-mono">{(s.ssh as { generated?: boolean }).generated ? "SSH key (private half stored encrypted)" : ""}</div>
              {(s.ssh as { generated?: boolean }).generated && <StatusBadge status={String((s.ssh as { status?: string }).status ?? "untested")} />}
              <div className="text-xs text-muted">set up below under Host Maintenance</div>
            </div>
            <div className="text-xs text-muted">Installs package updates through the restricted pyxie-maint wrapper. The PBS API can only list updates, not install them.</div>
          </div>
          <div className="border-t border-border pt-3 mt-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-proxmox mb-2">Host Maintenance — Connect a Host</div>
            <PbsSshConnect serverId={s.id} serverName={s.name} ssh={s.ssh as never} pending={s.pending ?? null} onChanged={load} />
            <details className="mt-3">
              <summary className="cursor-pointer select-none text-sm font-medium text-text">Install or remove the host wrapper (script you run on the backup server)</summary>
              <div className="mt-3"><PbsHostWrapperBuilder servers={[{ id: s.id, name: s.name }]} tlsMode={tlsMode} /></div>
            </details>
          </div>
          <div className="text-xs text-muted mt-2">Setup commands for the PBS accounts (<code>pyxie-pbs-ro@pbs</code> and <code>pyxie-pbs-admin@pbs</code>) are on the <Link href={`/operations/protection/servers/${s.id}`} className="text-accent hover:underline">server page, Access tab</Link>.</div>
        </Card>
      ))}
      {siteId && <PbsTargetForm siteId={siteId} />}
    </>
  );
}
