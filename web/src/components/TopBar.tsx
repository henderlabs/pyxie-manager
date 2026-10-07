"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import ThemeToggle from "@/components/ThemeToggle";
import { useMe } from "@/lib/useMe";

/** The strip at the top right of every page: which PyXie this is, who is signed in (with Sign out),
 * and the Light / Dark / Match-this-computer switch. */
export default function TopBar({ instance }: { instance: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useMe();
  if (pathname === "/login" || pathname.startsWith("/accept-invite")) return null;

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex items-center justify-end gap-3 mb-3 text-xs">
      <span className="font-semibold text-text" title="Which PyXie this is">{instance}</span>
      <span className="w-px h-4 bg-border" aria-hidden="true" />
      {me ? (
        <>
          <Link href="/profile" className="text-muted hover:text-text" title={`${me.email} — edit profile`}>
            {me.display_name || me.email}
          </Link>
          <button type="button" onClick={logout} className="text-accent hover:underline">
            Sign out
          </button>
        </>
      ) : (
        <span className="text-muted">Read-only</span>
      )}
      <ThemeToggle />
    </div>
  );
}
