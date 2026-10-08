import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET() {
  try {
    return NextResponse.json(await apiFetch("/api/auto-balance"));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
