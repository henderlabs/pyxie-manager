import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const qs = req.nextUrl.search;
    const result = await apiFetch(`/api/workloads/${params.id}/tasks${qs}`);
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
