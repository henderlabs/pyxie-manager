import { redirect } from "next/navigation";

// Retired 2026-09-15 -- merged into Logging (Audit Log + PVE Tasks +
// Internal Jobs, Event-Viewer-style channels on one page). Kept as a
// redirect, not deleted, so old bookmarks/links don't 404.
export default function AuditLogRedirect() {
  redirect("/platform/logging?channel=audit");
}
