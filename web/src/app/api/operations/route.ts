import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(req: NextRequest) {
  const limit = req.nextUrl.searchParams.get("limit") || "100";
  try {
    const result = await apiFetch(`/api/operations?limit=${limit}`);
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
