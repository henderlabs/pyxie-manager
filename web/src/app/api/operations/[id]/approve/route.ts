import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function POST(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const result = await apiFetch(`/api/operations/${params.id}/approve`, { method: "POST" });
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
