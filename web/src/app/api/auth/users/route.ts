import { NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET() {
  try {
    const result = await apiFetch("/api/auth/users");
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
