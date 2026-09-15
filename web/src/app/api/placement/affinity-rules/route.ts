import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET() {
  try {
    const result = await apiFetch("/api/placement/affinity-rules");
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  try {
    const result = await apiFetch("/api/placement/affinity-rules", { method: "POST", body: JSON.stringify(body) });
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
