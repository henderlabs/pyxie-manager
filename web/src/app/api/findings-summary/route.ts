import { NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";
import type { Finding } from "@/lib/api";

/** Lightweight endpoint for client components (e.g. the sidebar badge) that
 * can't call the backend API directly -- just the count they need, not the
 * full findings list. */
export async function GET() {
  try {
    const findings = await apiFetch<Finding[]>("/api/findings?active=true");
    const count = findings.filter((f) => f.severity === "critical" || f.severity === "warning").length;
    return NextResponse.json({ count });
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ count: 0 }, { status });
  }
}
