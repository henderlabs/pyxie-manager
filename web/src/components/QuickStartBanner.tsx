import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { SetupStatus } from "@/components/SetupGuide";

/** Shown on the Dashboard until the basics are connected (site, accounts, cluster). Costs one cheap database read. */
export default async function QuickStartBanner() {
  const status = await apiFetch<SetupStatus>("/api/setup/status").catch(() => null);
  if (!status) return null;
  const basics = status.steps.filter((s) => ["sites", "accounts", "cluster"].includes(s.key));
  if (basics.every((s) => s.state === "done")) return null;
  const next = status.steps.find((s) => s.state === "next");
  return (
    <div className="mb-4 px-4 py-3 rounded-lg border border-accent bg-accent/5 flex items-center gap-3 flex-wrap">
      <div className="text-sm text-text">
        <span className="font-medium">New here?</span>{" "}
        <span className="text-muted">{next ? `Next: ${next.title}.` : "Finish connecting your cluster."} The quick start walks you through it, including the two Proxmox tokens.</span>
      </div>
      <Link href="/platform/quick-start" className="ml-auto px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10">Open the quick start</Link>
    </div>
  );
}
