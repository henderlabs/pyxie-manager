import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET() {
  try {
    const me = await apiFetch("/api/auth/me");
    return NextResponse.json(me);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: "not authenticated" }, { status });
  }
}

export async function PATCH(req: NextRequest) {
  const body = await req.json();
  try {
    const me = await apiFetch("/api/auth/me", { method: "PATCH", body: JSON.stringify(body) });
    return NextResponse.json(me);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
