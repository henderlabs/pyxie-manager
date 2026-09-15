"use client";

import { useEffect } from "react";

export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  const isAuthError = /\b401\b/.test(error.message) || /not authenticated|invalid session|session expired/i.test(error.message);

  useEffect(() => {
    if (isAuthError) {
      window.location.href = "/login";
    }
  }, [isAuthError]);

  if (isAuthError) {
    return <div className="min-h-screen flex items-center justify-center bg-canvas text-muted text-sm">Session expired, redirecting to sign in…</div>;
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <div className="max-w-md text-center">
        <div className="text-text font-semibold mb-2">Something went wrong loading this page.</div>
        <div className="text-xs text-muted">{error.message}</div>
      </div>
    </div>
  );
}
