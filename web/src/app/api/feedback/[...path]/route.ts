import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

async function forward(req: NextRequest, path: string[], method: string) {
  const init: RequestInit = { method };
  if (method !== "GET") init.body = JSON.stringify(await req.json());
  try {
    return NextResponse.json(await apiFetch(`/api/feedback/${path.join("/")}`, init));
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}

export const GET = (r: NextRequest, c: { params: { path: string[] } }) => forward(r, c.params.path, "GET");
export const POST = (r: NextRequest, c: { params: { path: string[] } }) => forward(r, c.params.path, "POST");
export const PUT = (r: NextRequest, c: { params: { path: string[] } }) => forward(r, c.params.path, "PUT");
