import { NextRequest, NextResponse } from "next/server";
import { forwardedForHeader } from "@/lib/clientIp";
import { getSessionToken } from "@/lib/session";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

// Binary-safe passthrough of the single-file installer for signed-in admins (the same bytes a node gets from its link).
export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  const token = getSessionToken();
  const res = await fetch(`${BASE_URL}/api/pve-targets/${params.id}/host-kit.sh`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...forwardedForHeader() },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return NextResponse.json({ error: text || res.statusText }, { status: res.status });
  }
  return new NextResponse(await res.arrayBuffer(), {
    headers: { "Content-Type": "text/x-shellscript", "Content-Disposition": "attachment; filename=pyxie-host-kit.sh" },
  });
}
