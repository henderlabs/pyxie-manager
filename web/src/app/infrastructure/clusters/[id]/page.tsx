import { redirect } from "next/navigation";

// Same fix as infrastructure/sites/[id] -- an old cluster detail link
// (/infrastructure/clusters/<id>) fell through to Next's generic 404
// instead of the tree view.
export default function ClusterRedirect({ params }: { params: { id: string } }) {
  redirect(`/infrastructure/nodes?cluster=${params.id}`);
}
