import { NextRequest, NextResponse } from "next/server";
import { forwardedForHeader } from "@/lib/clientIp";
import { getSessionToken } from "@/lib/session";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

// Binary passthrough (xlsx / csv / zip) -- apiFetch() assumes JSON.
export async function GET(req: NextRequest) {
  const token = getSessionToken();
  const res = await fetch(`${BASE_URL}/api/reports/export${req.nextUrl.search}`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...forwardedForHeader() },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return NextResponse.json({ error: text || res.statusText }, { status: res.status });
  }
  return new NextResponse(await res.arrayBuffer(), {
    headers: {
      "Content-Type": res.headers.get("content-type") || "application/octet-stream",
      "Content-Disposition": res.headers.get("content-disposition") || "attachment",
    },
  });
}
