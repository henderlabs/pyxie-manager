import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  const after = req.nextUrl.searchParams.get("after") ?? "0";
  try {
    return NextResponse.json(await apiFetch(`/api/operations/${params.id}/log?after=${encodeURIComponent(after)}`, { cache: "no-store" }));
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
