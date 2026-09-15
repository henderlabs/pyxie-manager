import { NextResponse } from "next/server";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

export async function GET() {
  const res = await fetch(`${BASE_URL}/api/auth/bootstrap-status`, { cache: "no-store" });
  const data = await res.json();
  return NextResponse.json(data);
}
