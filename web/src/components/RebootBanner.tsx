"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { HostMaintenanceStatus } from "@/lib/api";
import { Card } from "@/components/Card";
import { ClockIcon } from "@/components/Icons";

// Isolated into its own client component so the rest of the Dashboard stays
// a fast, server-rendered page. host-maintenance-status (the SSH probe
// behind "needs a reboot") measured at 1-3+ seconds PER NODE (2026-09-12)
// -- it used to be fetched here server-side, blocking the entire Dashboard
// on it. Now it fetches after the page has already painted and this banner
// simply pops in a moment later, same tradeoff already made on
// Nodes/Maintenance.
export default function RebootBanner({ nodes }: { nodes: { id: string; name: string }[] }) {
  const [needsReboot, setNeedsReboot] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    let cancelled = false;
    Promise.all(
      nodes.map(async (n) => {
        try {
          const res = await fetch(`/api/nodes/${n.id}/host-maintenance-status`);
          const status = res.ok ? ((await res.json()) as HostMaintenanceStatus) : null;
          return status?.reboot_required ? n : null;
        } catch {
          return null;
        }
      })
    ).then((results) => {
      if (!cancelled) setNeedsReboot(results.filter((n): n is { id: string; name: string } => n !== null));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (needsReboot.length === 0) return null;

  return (
    <Card className="border-bad/40 flex-1 min-w-[280px]">
      <div className="flex items-center gap-2 text-sm text-bad">
        <ClockIcon className="w-4 h-4" />
        <span className="font-medium">
          {needsReboot.map((n) => n.name).join(", ")} {needsReboot.length === 1 ? "needs" : "need"} a reboot
        </span>
        <Link href="/operations/maintenance" className="text-xs text-accent hover:underline ml-auto">
          Go to Maintenance →
        </Link>
      </div>
    </Card>
  );
}
