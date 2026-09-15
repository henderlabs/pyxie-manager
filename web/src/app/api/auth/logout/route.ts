import { NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";
import { SESSION_COOKIE } from "@/lib/session";

export async function POST() {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
  }
  const res = NextResponse.json({ status: "ok" });
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
