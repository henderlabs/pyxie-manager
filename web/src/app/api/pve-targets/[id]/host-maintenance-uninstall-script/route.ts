import { NextRequest, NextResponse } from "next/server";
import { forwardedForHeader } from "@/lib/clientIp";
import { getSessionToken } from "@/lib/session";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

// Binary/text passthrough, same pattern as the host-maintenance-kit route.
export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  const token = getSessionToken();
  const res = await fetch(`${BASE_URL}/api/pve-targets/${params.id}/host-maintenance-uninstall-script`, {
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
      "Content-Type": "text/x-shellscript",
      "Content-Disposition": res.headers.get("content-disposition") || "attachment; filename=uninstall.sh",
    },
  });
}
