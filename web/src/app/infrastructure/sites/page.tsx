import { redirect } from "next/navigation";

// Retired 2026-09-12 -- folded into Hosts & Clusters (click a site in its
// tree instead). Kept as a redirect, not deleted, so old bookmarks/links
// don't 404.
export default function SitesPage() {
  redirect("/infrastructure/nodes");
}
