import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  const force = req.nextUrl.searchParams.get("force") === "true";
  try {
    const result = await apiFetch(
      `/api/pve-targets/${params.id}/host-maintenance-credential/generate${force ? "?force=true" : ""}`,
      { method: "POST" }
    );
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
