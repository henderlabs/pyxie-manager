import { apiFetch } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import { ReportIcon } from "@/components/Icons";
import ReportingWorkspace from "@/components/ReportingWorkspace";

export default async function ReportingPage() {
  const [catalog, options, status] = await Promise.all([
    apiFetch<any>("/api/reports/catalog"),
    apiFetch<any>("/api/reports/options"),
    apiFetch<any>("/api/reports/status").catch(() => null),
  ]);
  return (
    <div>
      <PageHeader
        title="Reporting"
        subtitle="RVTools-style inventory reports — pick a scope, search, and export to Excel or CSV"
        icon={<ReportIcon className="w-5 h-5" />}
      />
      <ReportingWorkspace catalog={catalog} options={options} initialStatus={status} />
    </div>
  );
}
