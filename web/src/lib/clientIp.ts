import { headers } from "next/headers";

// The reverse proxy (Caddy) sets X-Forwarded-For to the real client address and
// overwrites anything a client sent itself. Pass just that first address on to
// the API, which only trusts it from the private Docker network.
export function forwardedForHeader(): Record<string, string> {
  try {
    const ip = headers().get("x-forwarded-for")?.split(",")[0].trim();
    return ip ? { "X-Forwarded-For": ip } : {};
  } catch {
    return {};
  }
}
