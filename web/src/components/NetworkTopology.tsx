import type { NodeNetworkTopology } from "@/lib/api";
import { Card, CardTitle, NoInfrastructureHint } from "@/components/Card";

// Read-only. No write path exists for host-level network config here on
// purpose -- a bad apply against /nodes/{node}/network can sever that
// node's own connectivity, a different risk class from every other PVE
// write PyXie makes.
export default function NetworkTopology({ topology }: { topology: NodeNetworkTopology[] }) {
  if (topology.length === 0) {
    return (
      <div className="text-sm text-muted">
        <NoInfrastructureHint subject="nodes" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {topology.map((t) => (
        <Card key={t.node_id}>
          <CardTitle>
            {t.node} <span className="text-text/50 font-normal">· {t.cluster_name}</span>
          </CardTitle>
          {t.error ? (
            <div className="text-xs text-bad">{t.error}</div>
          ) : t.interfaces.length === 0 ? (
            <div className="text-xs text-muted">No interfaces reported.</div>
          ) : (
            <div className="divide-y divide-border">
              {t.interfaces.map((i) => (
                <div key={i.iface} className="py-1.5 flex items-center justify-between gap-2 text-sm">
                  <div className="min-w-0">
                    <span className="text-text font-mono">{i.iface}</span>
                    <span className="text-muted text-xs ml-2">{i.type}</span>
                    {i.ports && <span className="text-muted/70 text-xs ml-2 truncate">({i.ports})</span>}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {!i.active && (
                      <span className="text-[10px] uppercase tracking-wide border border-border rounded px-1 py-0.5 text-muted">
                        inactive
                      </span>
                    )}
                    {i.vlan_aware ? (
                      <span
                        className="text-[10px] uppercase tracking-wide border border-good/40 text-good rounded px-1 py-0.5"
                        title={i.allowed_vlans ? `Allowed VLANs: ${i.allowed_vlans}` : "VLAN-aware, no explicit allow-list (any VLAN 1-4094)"}
                      >
                        VLAN-aware{i.allowed_vlans ? ` (${i.allowed_vlans})` : ""}
                      </span>
                    ) : (
                      <span className="text-[10px] uppercase tracking-wide border border-border rounded px-1 py-0.5 text-muted/60">
                        untagged only
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}
