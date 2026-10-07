import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function POST(req: NextRequest) {
  const body = await req.text();
  try {
    const result = await apiFetch("/api/system/updates/rollback", { method: "POST", body: body || undefined });
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    let message = (e as Error).message;
    try {
      message = JSON.parse(message).detail || message;
    } catch {
      // not JSON: keep the text
    }
    return NextResponse.json({ error: message }, { status });
  }
}
