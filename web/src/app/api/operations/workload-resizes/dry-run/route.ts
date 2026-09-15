import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const result = await apiFetch("/api/operations/workload-resizes/dry-run", { method: "POST", body: JSON.stringify(body) });
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
