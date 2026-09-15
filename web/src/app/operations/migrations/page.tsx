import { redirect } from "next/navigation";

// Retired 2026-09-11 -- live migration is now Maintenance's own "Balance
// Load" trigger (Nodes section) and Workloads' "Move to Another Node"
// action, both built on the exact same migration workflow this page used.
// Kept as a redirect (not deleted outright) so any old bookmark/link still
// lands somewhere real instead of a 404.
export default function MigrationsPage() {
  redirect("/operations/maintenance");
}
