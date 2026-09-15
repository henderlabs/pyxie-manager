import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  const body = await req.json();
  try {
    const created = await apiFetch(`/api/pve-targets/${params.id}/credentials`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(created);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
