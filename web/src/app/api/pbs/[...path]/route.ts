import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const fail = (e: unknown) => NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
const ID = "[0-9a-fA-F-]{36}";
const ALLOWED: Record<string, RegExp[]> = {
  GET: [/^servers$/, /^namespaces$/, new RegExp(`^servers/${ID}$`), new RegExp(`^servers/${ID}/credentials$`)],
  POST: [
    new RegExp(`^servers/${ID}/(refresh|test-access|ssh/generate|ssh/probe|ssh/check|host-kit/link|jobs/dry-run|datastores/dry-run|storage-add/dry-run|update/dry-run|reboot/dry-run|maintenance/dry-run)$`),
  ],
  PUT: [new RegExp(`^servers/${ID}/(credentials/(inventory|administrative|restore|backup)|ssh-host-key)$`)],
  DELETE: [new RegExp(`^servers/${ID}/(ssh-host-key|credentials/(inventory|administrative|restore|backup))$`)],
};

async function handle(req: NextRequest, params: { path: string[] }) {
  const path = params.path.join("/");
  if (!(ALLOWED[req.method] ?? []).some((r) => r.test(path))) return NextResponse.json({ error: "not found" }, { status: 404 });
  const qs = req.nextUrl.search;
  const init: RequestInit = { method: req.method };
  if (req.method === "POST" || req.method === "PUT") init.body = JSON.stringify(await req.json().catch(() => ({})));
  try { return NextResponse.json(await apiFetch(`/api/pbs/${path}${qs}`, init)); } catch (e) { return fail(e); }
}

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) { return handle(req, params); }
export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) { return handle(req, params); }
export async function PUT(req: NextRequest, { params }: { params: { path: string[] } }) { return handle(req, params); }
export async function DELETE(req: NextRequest, { params }: { params: { path: string[] } }) { return handle(req, params); }
