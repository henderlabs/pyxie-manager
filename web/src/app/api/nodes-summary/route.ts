import { NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";
import type { HostMaintenanceStatus, Node } from "@/lib/api";

/** For the Nodes nav badge: how many nodes currently need a reboot (Stage
 * W4, live SSH check). Unprovisioned nodes fail the status check fast and
 * just don't count -- this only flags nodes PyXie actually knows need
 * action right now, never a guess. */
export async function GET() {
  try {
    const nodes = await apiFetch<Node[]>("/api/nodes");
    const statuses = await Promise.all(
      nodes.map((n) =>
        apiFetch<HostMaintenanceStatus>(`/api/nodes/${n.id}/host-maintenance-status`).catch(() => null)
      )
    );
    const count = statuses.filter((s) => s?.reboot_required).length;
    return NextResponse.json({ count });
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ count: 0 }, { status });
  }
}
