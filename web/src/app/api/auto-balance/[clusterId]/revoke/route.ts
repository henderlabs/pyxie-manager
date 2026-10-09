import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

// The off switch for auto-approve (see api/app/routers/auto_balance.py): back to Recommend only at once.
export async function POST(_req: NextRequest, { params }: { params: { clusterId: string } }) {
  try {
    return NextResponse.json(await apiFetch(`/api/auto-balance/${params.clusterId}/revoke`, { method: "POST" }));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
