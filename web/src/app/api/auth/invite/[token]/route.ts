import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(_req: NextRequest, { params }: { params: { token: string } }) {
  try {
    const result = await apiFetch(`/api/auth/invite/${params.token}`);
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
