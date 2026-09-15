import { NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";
import type { Finding, Operation, Recommendation } from "@/lib/api";

/** For the Maintenance nav badge: operations literally waiting on a click,
 * active placement-rule violations a migration could fix, plus the
 * cluster/node-level recommendation categories (updates, capacity,
 * load-balancing) that live on Maintenance's own Recommendations column as
 * of 2026-09-12 -- all of these are "there's an action available to take
 * on Maintenance right now." Rightsizing counts toward its own page's
 * badge instead (see recommendations-summary/route.ts). */
export async function GET() {
  try {
    const [operations, findings, recommendations] = await Promise.all([
      apiFetch<Operation[]>("/api/operations?limit=200"),
      apiFetch<Finding[]>("/api/findings?active=true&category=placement"),
      apiFetch<Recommendation[]>("/api/recommendations"),
    ]);
    const awaitingApproval = operations.filter((o) => o.status === "awaiting_approval" && !o.dismissed).length;
    const maintenanceRecs = recommendations.filter(
      (r) => r.category !== "rightsizing" && r.lifecycle_state === "open"
    ).length;
    return NextResponse.json({ count: awaitingApproval + findings.length + maintenanceRecs });
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ count: 0 }, { status });
  }
}
