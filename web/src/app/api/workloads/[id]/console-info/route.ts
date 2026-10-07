import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    return NextResponse.json(await apiFetch(`/api/workloads/${params.id}/console-info`));
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
