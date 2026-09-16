import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function PATCH(req: NextRequest, { params }: { params: { id: string; credId: string } }) {
  const body = await req.json();
  try {
    const updated = await apiFetch(`/api/pve-targets/${params.id}/credentials/${params.credId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    return NextResponse.json(updated);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
