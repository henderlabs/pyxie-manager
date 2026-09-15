import { NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";
import type { Recommendation } from "@/lib/api";

// Narrowed 2026-09-12 to rightsizing only, matching the page it badges --
// updates/capacity/placement now live on Maintenance and count toward its
// own badge instead (see operations-summary/route.ts).
export async function GET() {
  try {
    const recommendations = await apiFetch<Recommendation[]>("/api/recommendations?category=rightsizing");
    const count = recommendations.filter((r) => r.lifecycle_state === "open").length;
    return NextResponse.json({ count });
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ count: 0 }, { status });
  }
}
