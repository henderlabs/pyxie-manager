import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function PATCH(req: NextRequest, { params }: { params: { id: string } }) {
  const body = await req.json();
  try {
    const updated = await apiFetch(`/api/pve-targets/${params.id}/endpoints`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    return NextResponse.json(updated);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    let message = (e as Error).message;
    try {
      message = JSON.parse(message).detail ?? message;
    } catch {
      /* not JSON */
    }
    return NextResponse.json({ error: message }, { status });
  }
}
