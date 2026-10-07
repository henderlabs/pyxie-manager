import { apiFetch, ApiError } from "@/lib/api";
import { PageHeader, Card } from "@/components/Card";
import UpdatesPanel from "@/components/UpdatesPanel";
import { PackageIcon } from "@/components/Icons";
import type { UpdatesResponse } from "@/lib/updates";

export default async function UpdatesPage() {
  let initial: UpdatesResponse | null = null;
  let denied = false;
  try {
    initial = await apiFetch<UpdatesResponse>("/api/system/updates");
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) denied = true;
    else throw e;
  }
  return (
    <div>
      <PageHeader title="Updates" subtitle="Settings · see new releases and update this server" icon={<PackageIcon className="w-5 h-5" />} />
      {denied || !initial ? (
        <Card>
          <div className="text-sm text-muted">Only administrators can see or run updates.</div>
        </Card>
      ) : (
        <UpdatesPanel initial={initial} />
      )}
    </div>
  );
}
