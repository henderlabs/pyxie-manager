import { apiFetch } from "@/lib/api";
import type { Finding } from "@/lib/api";
import { Card, EmptyState, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { HealthIcon } from "@/components/Icons";

export default async function HealthPage({ searchParams }: { searchParams: { active?: string } }) {
  const showResolved = searchParams.active === "false";
  const findings = await apiFetch<Finding[]>(`/api/findings${showResolved ? "" : "?active=true"}`);

  return (
    <div>
      <PageHeader title="Health" subtitle="Read-only findings, not recommendations" icon={<HealthIcon className="w-5 h-5" />} />
      <div className="flex gap-2 mb-4 text-sm">
        <a href="/operations/health" className={`px-3 py-1 rounded border ${!showResolved ? "border-accent text-accent bg-accent/10" : "border-border text-muted"}`}>
          Active
        </a>
        <a href="/operations/health?active=false" className={`px-3 py-1 rounded border ${showResolved ? "border-accent text-accent bg-accent/10" : "border-border text-muted"}`}>
          All (including resolved)
        </a>
      </div>
      <Card>
        {findings.length === 0 ? (
          <EmptyState message="No active health findings. Everything discovered so far looks healthy." />
        ) : (
          <div className="divide-y divide-border">
            {findings.map((f) => (
              <div key={f.id} className="py-2 text-sm">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <StatusBadge status={f.active ? f.severity : "unknown"} />
                    <span className="text-text">{f.title}</span>
                  </div>
                  <span className="text-xs text-muted uppercase">{f.category}</span>
                </div>
                <div className="text-xs text-muted mt-0.5 ml-[70px]">
                  first seen {new Date(f.first_observed).toLocaleString()} · last seen {new Date(f.last_observed).toLocaleString()}
                  {!f.active && f.resolved_at && <> · resolved {new Date(f.resolved_at).toLocaleString()}</>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
