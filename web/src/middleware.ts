import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/session";

// Cheap, cookie-presence-only gate for page navigation UX. The real
// enforcement is server-side: every API router (except /api/auth/* and
// the Docker healthcheck) requires a valid session via get_current_user,
// so a forged/absent cookie never actually reaches protected data --
// this middleware only avoids a flash of a page that will fail to load.
// Static assets under public/ (logo, favicon, fonts...) must load on the
// unauthenticated login/accept-invite pages themselves -- without this,
// every such request had no session cookie yet either and got redirected
// to the /login HTML document instead of the actual file, a real gap
// found live (the actual page's own logo was the symptom, this
// middleware gap was the cause). Checked in the function body rather
// than the matcher config below, since Next's matcher only supports a
// limited path-to-regexp syntax, not an arbitrary extension-alternation
// regex.
const STATIC_ASSET_RE = /\.(png|jpe?g|gif|svg|webp|ico|css|js|map|woff2?|ttf)$/i;

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (
    pathname.startsWith("/login") ||
    pathname.startsWith("/accept-invite") ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api/auth") ||
    STATIC_ASSET_RE.test(pathname)
  ) {
    return NextResponse.next();
  }
  const hasSession = req.cookies.has(SESSION_COOKIE);
  if (!hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    // Behind a reverse proxy, rebuild the origin from the forwarded headers so
    // the redirect never points at the container-internal host/port.
    const proto = req.headers.get("x-forwarded-proto");
    const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
    if (proto) url.protocol = proto;
    if (host) {
      url.port = ""; // drop the container-internal port; host may carry its own
      url.host = host;
    }
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
