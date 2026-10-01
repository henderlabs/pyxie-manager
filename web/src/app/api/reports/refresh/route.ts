import { NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function POST() {
  try {
    return NextResponse.json(await apiFetch("/api/reports/refresh", { method: "POST" }));
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
