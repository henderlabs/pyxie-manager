import { cookies } from "next/headers";

export const SESSION_COOKIE = "pyxie_session";

export function getSessionToken(): string | undefined {
  try {
    return cookies().get(SESSION_COOKIE)?.value;
  } catch {
    // cookies() throws if called outside a request scope (shouldn't happen
    // in practice here, but fail closed rather than crash the render).
    return undefined;
  }
}
