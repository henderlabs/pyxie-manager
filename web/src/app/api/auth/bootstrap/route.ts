import { NextRequest, NextResponse } from "next/server";
import { forwardedForHeader } from "@/lib/clientIp";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const res = await fetch(`${BASE_URL}/api/auth/bootstrap`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...forwardedForHeader() },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json({ error: data.detail || "Bootstrap failed" }, { status: res.status });
  }
  return NextResponse.json(data);
}
