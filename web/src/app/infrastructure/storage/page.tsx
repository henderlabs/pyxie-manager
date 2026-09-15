import { apiFetch } from "@/lib/api";
import type { StorageItem } from "@/lib/api";
import { PageHeader, StatTile } from "@/components/Card";
import StorageTable from "@/components/tables/StorageTable";
import { StorageIcon } from "@/components/Icons";
import { formatBytes } from "@/lib/format";

export default async function StoragePage() {
  const storage = await apiFetch<StorageItem[]>("/api/storage");

  // Excludes is_missing -- a storage entry PVE no longer reports isn't
  // real capacity. Summary tiles show "if available" for Storage, same
  // idea as Workloads/Hosts & Clusters.
  const present = storage.filter((s) => !s.is_missing);
  const totalCapacity = present.reduce((sum, s) => sum + (s.capacity_bytes ?? 0), 0);
  const totalUsed = present.reduce((sum, s) => sum + (s.used_bytes ?? 0), 0);

  return (
    <div>
      <PageHeader title="Storage" icon={<StorageIcon className="w-5 h-5" />} />
      <div className="grid grid-cols-4 gap-3 mb-5">
        <StatTile label="Storage Pools" value={present.length} />
        <StatTile label="Total Capacity" value={formatBytes(totalCapacity)} />
        <StatTile label="Total Used" value={formatBytes(totalUsed)} />
        <StatTile label="Free" value={formatBytes(totalCapacity - totalUsed)} />
      </div>
      <StorageTable storage={storage} />
    </div>
  );
}
