import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function PUT(req: NextRequest, { params }: { params: { clusterId: string } }) {
  const body = await req.json();
  try {
    return NextResponse.json(await apiFetch(`/api/auto-balance/${params.clusterId}`, { method: "PUT", body: JSON.stringify(body) }));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
