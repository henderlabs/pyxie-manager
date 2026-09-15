"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function AcceptInviteForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";
  const [checking, setChecking] = useState(true);
  const [invite, setInvite] = useState<{ email: string; display_name: string | null } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) {
      setInvalid(true);
      setChecking(false);
      return;
    }
    fetch(`/api/auth/invite/${token}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setInvite)
      .catch(() => setInvalid(true))
      .finally(() => setChecking(false));
  }, [token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("Passwords don't match");
      return;
    }
    setPending(true);
    try {
      const res = await fetch("/api/auth/accept-invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Failed");
        return;
      }
      setDone(true);
      setTimeout(() => router.push("/login"), 1500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (checking) {
    return <div className="text-muted text-sm">Loading…</div>;
  }

  if (invalid) {
    return (
      <div className="text-bad text-sm">
        This invite link is invalid or has expired. Ask an admin to send a new one.
      </div>
    );
  }

  if (done) {
    return <div className="text-good text-sm">Password set — redirecting to sign in…</div>;
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="text-xs text-muted">Setting up {invite?.email}</div>
      <Field label="Password">
        <input
          type="password"
          required
          minLength={10}
          autoFocus
          className="input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </Field>
      <Field label="Confirm password">
        <input
          type="password"
          required
          minLength={10}
          className="input"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </Field>
      <div className="text-xs text-muted">At least 10 characters.</div>
      {error && <div className="text-xs text-bad">{error}</div>}
      <button
        type="submit"
        disabled={pending}
        className="w-full px-3 py-2 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
      >
        {pending ? "Working…" : "Set password & continue"}
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
  );
}

export default function AcceptInvitePage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <div className="w-full max-w-sm bg-surface border border-border rounded-lg p-6 space-y-4">
        <div>
          <img src="/pyxie-logo.png" alt="PyXie — Proxmox Operations" className="h-10 w-auto mx-auto block" />
          <div className="text-xs text-muted mt-2">Accept invite</div>
        </div>
        <Suspense fallback={<div className="text-muted text-sm">Loading…</div>}>
          <AcceptInviteForm />
        </Suspense>
      </div>
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
