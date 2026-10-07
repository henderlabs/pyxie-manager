import { NextRequest, NextResponse } from "next/server";
import { forwardedForHeader } from "@/lib/clientIp";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

// Public on purpose (a PVE node fetches this with curl, no login): the link is short-lived and the file holds only
// scripts and a public key. Integrity is the SHA-256 the admin sees in PyXie, checked on the host.
export async function GET(_req: NextRequest, { params }: { params: { token: string } }) {
  const res = await fetch(`${BASE_URL}/api/host-kit/${encodeURIComponent(params.token)}`, {
    headers: { ...forwardedForHeader() },
    cache: "no-store",
  });
  if (!res.ok) return new NextResponse("link expired or not found\n", { status: res.status, headers: { "Content-Type": "text/plain" } });
  return new NextResponse(await res.arrayBuffer(), {
    headers: { "Content-Type": "text/plain", "Content-Disposition": "attachment; filename=pyxie-host-kit.sh", "Cache-Control": "no-store" },
  });
}
