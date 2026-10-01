import { NextRequest, NextResponse } from "next/server";
import { forwardedForHeader } from "@/lib/clientIp";
import { getSessionToken } from "@/lib/session";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

// Binary passthrough -- apiFetch() assumes JSON, so this proxies the
// tar.gz kit download directly rather than going through it.
export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  const token = getSessionToken();
  const res = await fetch(`${BASE_URL}/api/pve-targets/${params.id}/host-maintenance-kit`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...forwardedForHeader() },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return NextResponse.json({ error: text || res.statusText }, { status: res.status });
  }
  const buf = await res.arrayBuffer();
  return new NextResponse(buf, {
    headers: {
      "Content-Type": "application/gzip",
      "Content-Disposition": res.headers.get("content-disposition") || "attachment; filename=pyxie-hostmaint-kit.tar.gz",
    },
  });
}
