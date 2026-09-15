import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function PUT(req: NextRequest) {
  const body = await req.json();
  try {
    const result = await apiFetch("/api/policies", { method: "PUT", body: JSON.stringify(body) });
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
