"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [needsBootstrap, setNeedsBootstrap] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    fetch("/api/auth/bootstrap-status")
      .then((r) => r.json())
      .then((d) => setNeedsBootstrap(!!d.needs_bootstrap))
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const url = needsBootstrap ? "/api/auth/bootstrap" : "/api/auth/login";
      const body = needsBootstrap ? { email, password, display_name: displayName || undefined } : { email, password };
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Failed");
        return;
      }
      if (needsBootstrap) {
        // bootstrap creates the account but doesn't log in -- do that next.
        const loginRes = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        if (!loginRes.ok) {
          setError("Account created -- please log in.");
          setNeedsBootstrap(false);
          return;
        }
      }
      router.push("/");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (checking) {
    return <div className="min-h-screen flex items-center justify-center bg-canvas text-muted text-sm">Loading…</div>;
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <form onSubmit={submit} className="w-full max-w-sm bg-surface border border-border rounded-lg p-6 space-y-4">
        <div>
          <img src="/pyxie-logo.png" alt="PyXie — Proxmox Operations" className="h-10 w-auto mx-auto block" />
          <div className="text-xs text-muted mt-2">
            {needsBootstrap ? "Create the administrator account" : "Sign in"}
          </div>
        </div>

        {needsBootstrap && (
          <Field label="Display name (optional)">
            <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </Field>
        )}
        <Field label="Email">
          <input
            type="email"
            required
            autoFocus
            className="input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="Password">
          <input
            type="password"
            required
            minLength={needsBootstrap ? 10 : undefined}
            className="input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {needsBootstrap && <div className="text-xs text-muted">At least 10 characters.</div>}
        {error && <div className="text-xs text-bad">{error}</div>}
        <button
          type="submit"
          disabled={pending}
          className="w-full px-3 py-2 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Working…" : needsBootstrap ? "Create account & sign in" : "Sign in"}
        </button>
        <style jsx>{`
          .input {
            width: 100%;
            background: #0b0e14;
            border: 1px solid #232a38;
            border-radius: 6px;
            padding: 6px 8px;
            font-size: 0.875rem;
            color: #e6e9ef;
          }
        `}</style>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs text-muted space-y-1">
      <span>{label}</span>
      {children}
    </label>
  );
}
