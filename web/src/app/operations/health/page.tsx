import { apiFetch } from "@/lib/api";
import type { Finding } from "@/lib/api";
import { Card, EmptyState, PageHeader } from "@/components/Card";
import FindingList from "@/components/FindingList";
import { HealthIcon } from "@/components/Icons";

type View = "active" | "acknowledged" | "dismissed" | "resolved";

export default async function HealthPage({ searchParams }: { searchParams: { view?: string; active?: string } }) {
  // ?active=false was the old "All (including resolved)" link; keep it working.
  const requested = searchParams.view ?? (searchParams.active === "false" ? "resolved" : "active");
  const view: View = (["active", "acknowledged", "dismissed", "resolved"] as const).find((v) => v === requested) ?? "active";

  const [current, resolved] = await Promise.all([
    apiFetch<Finding[]>("/api/findings?active=true&status=any"),
    view === "resolved" ? apiFetch<Finding[]>("/api/findings?active=false") : Promise.resolve([] as Finding[]),
  ]);
  const counts = {
    active: current.filter((f) => f.triage === "open").length,
    acknowledged: current.filter((f) => f.triage === "acknowledged").length,
    dismissed: current.filter((f) => f.triage === "dismissed").length,
  };
  const shown = view === "resolved" ? resolved : current.filter((f) => f.triage === (view === "active" ? "open" : view));
  const chips: { key: View; label: string }[] = [
    { key: "active", label: `Active (${counts.active})` },
    { key: "acknowledged", label: `Acknowledged (${counts.acknowledged})` },
    { key: "dismissed", label: `Dismissed (${counts.dismissed})` },
    { key: "resolved", label: "Resolved" },
  ];
  const empty: Record<View, string> = {
    active: counts.acknowledged + counts.dismissed > 0
      ? "Nothing needs attention. Acknowledged and dismissed findings are under their own tabs."
      : "No active health findings. Everything discovered so far looks healthy.",
    acknowledged: "No acknowledged findings.",
    dismissed: "No dismissed findings.",
    resolved: "No resolved findings.",
  };

  return (
    <div>
      <PageHeader title="Health" subtitle="Read-only findings, not recommendations" icon={<HealthIcon className="w-5 h-5" />} />
      <div className="flex gap-2 mb-4 text-sm flex-wrap">
        {chips.map((c) => (
          <a key={c.key} href={c.key === "active" ? "/operations/health" : `/operations/health?view=${c.key}`}
            className={`px-3 py-1 rounded border ${view === c.key ? "border-accent text-accent bg-accent/10" : "border-border text-muted"}`}>
            {c.label}
          </a>
        ))}
      </div>
      <Card>
        {shown.length === 0 ? <EmptyState message={empty[view]} /> : <FindingList key={view} findings={shown} view={view} />}
      </Card>
    </div>
  );
}
