import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/session";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const backendRes = await fetch(`${BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await backendRes.json().catch(() => ({}));
  if (!backendRes.ok) {
    return NextResponse.json({ error: data.detail || "Login failed" }, { status: backendRes.status });
  }

  const res = NextResponse.json({ user: data.user });
  res.cookies.set(SESSION_COOKIE, data.token, {
    httpOnly: true,
    // This app is deliberately plain HTTP, LAN-only (see README) -- Secure
    // would make the browser silently refuse to send the cookie at all.
    secure: false,
    sameSite: "lax",
    path: "/",
    expires: new Date(data.expires_at),
  });
  return res;
}
