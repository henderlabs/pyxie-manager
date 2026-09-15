import { redirect } from "next/navigation";

// A site used to have its own detail URL (/infrastructure/sites/<id>).
// Folded into Hosts & Clusters 2026-09-12, but the bare /infrastructure/sites
// redirect doesn't catch an old bookmark/link that still carries the id --
// that fell through to Next's generic 404 instead of landing anywhere useful.
// Send it to the same tree view a click on
// this site there would produce.
export default function SiteRedirect({ params }: { params: { id: string } }) {
  redirect(`/infrastructure/nodes?site=${params.id}`);
}
