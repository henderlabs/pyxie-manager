import Link from "next/link";

export function Card({ children, className = "", id }: { children: React.ReactNode; className?: string; id?: string }) {
  return <div id={id} className={`bg-surface border border-border rounded-lg p-4 scroll-mt-4 ${className}`}>{children}</div>;
}

export function CardTitle({ children }: { children: React.ReactNode }) {
  return <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">{children}</div>;
}

export function StatTile({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <Card>
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted mb-2">
        {icon && <span className="text-proxmox/70">{icon}</span>}
        {label}
      </div>
      <div className="text-2xl font-semibold text-text">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </Card>
  );
}

export function EmptyState({ message }: { message: React.ReactNode }) {
  return <div className="text-sm text-muted text-center py-10">{message}</div>;
}

/** Standard "nothing discovered yet" copy for a page that depends on a PVE
 * target being configured -- points a brand-new deployment at the one setup
 * step it's actually missing, instead of leaving a bare "no data" message. */
export function NoInfrastructureHint({ subject }: { subject: string }) {
  return (
    <>
      No {subject} discovered yet.{" "}
      <Link href="/platform/providers" className="text-accent hover:underline">
        Configure a PVE target
      </Link>
      .
    </>
  );
}

export function PageHeader({
  title,
  subtitle,
  icon,
}: {
  title: React.ReactNode;
  subtitle?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="mb-5">
      <h1 className="text-xl font-semibold text-text flex items-center gap-2">
        {icon && <span className="text-proxmox">{icon}</span>}
        {title}
      </h1>
      {subtitle && <p className="text-sm text-muted mt-0.5">{subtitle}</p>}
    </div>
  );
}
