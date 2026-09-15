// A node running with configured VM memory close to (or past) its physical
// total is sometimes deliberate -- PVE ballooning lets you overcommit RAM
// on purpose -- but it means less real headroom for migrations/failovers
// than the raw "online" status suggests, so it needs its own visible
// warning rather than silently being fine or silently being a crisis:
// a host can legitimately need to run that tightly overprovisioned, as
// long as it's clearly warned about on both the Maintenance and Hosts &
// Clusters pages. 90% mirrors the threshold already used for the
// cluster-wide memory-allocation capacity recommendation.
export const OVERPROVISIONED_THRESHOLD_PCT = 90;

export function overprovisionedPct(node: {
  allocated_memory_bytes: number | null | undefined;
  mem_total_bytes: number | null | undefined;
}): number | null {
  if (!node.mem_total_bytes || node.allocated_memory_bytes == null) return null;
  return (node.allocated_memory_bytes / node.mem_total_bytes) * 100;
}

export function isOverprovisioned(node: {
  allocated_memory_bytes: number | null | undefined;
  mem_total_bytes: number | null | undefined;
}): boolean {
  const pct = overprovisionedPct(node);
  return pct != null && pct >= OVERPROVISIONED_THRESHOLD_PCT;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes === 0) return "0 B";
  if (bytes < 0) return `-${formatBytes(-bytes)}`; // e.g. negative headroom on an over-committed node
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

export function formatPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return "—";
  return `${pct.toFixed(0)}%`;
}

export function formatUptime(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
