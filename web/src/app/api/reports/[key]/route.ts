import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(req: NextRequest, { params }: { params: { key: string } }) {
  try {
    const data = await apiFetch(`/api/reports/${encodeURIComponent(params.key)}${req.nextUrl.search}`);
    return NextResponse.json(data);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
