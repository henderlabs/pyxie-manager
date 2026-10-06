"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { UserAccount } from "@/lib/api";

export default function UsersTable({ initial }: { initial: UserAccount[] }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reinviteLink, setReinviteLink] = useState<{ id: string; link: string; emailSent: boolean; emailError: string | null } | null>(
    null
  );
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function patch(id: string, body: Record<string, boolean>) {
    setBusyId(id);
    setError(null);
    try {
      const res = await fetch(`/api/auth/users/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function reinvite(id: string) {
    setBusyId(id);
    setError(null);
    setReinviteLink(null);
    try {
      const res = await fetch(`/api/auth/users/${id}/reinvite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invite_base_url: window.location.origin }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setReinviteLink({
        id,
        link: `${window.location.origin}/accept-invite?token=${data.invite_token}`,
        emailSent: !!data.email_sent,
        emailError: data.email_error || null,
      });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function deleteUser(id: string) {
    setBusyId(id);
    setError(null);
    try {
      const res = await fetch(`/api/auth/users/${id}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setConfirmDeleteId(null);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      {error && <div className="text-xs text-bad mb-3">{error}</div>}
      <div className="divide-y divide-border">
        {initial.map((u) => (
          <div key={u.id} className="py-2.5">
            <div className="flex items-center justify-between text-sm gap-3">
              <div className="min-w-0">
                <div className="text-text font-medium truncate">
                  {u.display_name || u.email}
                  <span
                    className={`ml-2 text-[10px] uppercase tracking-wide border rounded px-1 py-0.5 ${
                      u.is_admin ? "border-accent text-accent" : "border-border text-muted"
                    }`}
                    title={u.is_admin ? "Full access, including managing other users" : "Read-only access"}
                  >
                    {u.is_admin ? "admin" : "viewer"}
                  </span>
                  {u.pending_invite && (
                    <span className="ml-2 text-[10px] uppercase tracking-wide border border-border rounded px-1 py-0.5 text-muted">
                      invite pending
                    </span>
                  )}
                  {!u.is_active && (
                    <span className="ml-2 text-[10px] uppercase tracking-wide border border-bad text-bad rounded px-1 py-0.5">
                      deactivated
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted truncate">{u.email}</div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  disabled={busyId === u.id}
                  onClick={() => patch(u.id, { is_admin: !u.is_admin })}
                  title={u.is_admin ? "Remove admin access -- they keep read-only access" : "Grant full access, including managing other users"}
                  className="px-2 py-1 rounded text-xs font-medium bg-surface text-text border border-border hover:bg-surface2 disabled:opacity-50"
                >
                  {u.is_admin ? "Change to Viewer" : "Change to Admin"}
                </button>
                <button
                  disabled={busyId === u.id}
                  onClick={() => patch(u.id, { is_active: !u.is_active })}
                  className="px-2 py-1 rounded text-xs font-medium bg-surface text-text border border-border hover:bg-surface2 disabled:opacity-50"
                >
                  {u.is_active ? "Deactivate" : "Reactivate"}
                </button>
                {u.pending_invite && (
                  <button
                    disabled={busyId === u.id}
                    onClick={() => reinvite(u.id)}
                    className="px-2 py-1 rounded text-xs font-medium bg-surface text-text border border-border hover:bg-surface2 disabled:opacity-50"
                  >
                    Reinvite
                  </button>
                )}
                {!u.is_active && (
                  <button
                    disabled={busyId === u.id}
                    onClick={() => setConfirmDeleteId(u.id)}
                    title="Permanently removes this account. Deactivated users can't sign in, but stay listed until deleted -- unlike everything else in PyXie (sites, nodes, workloads), this is a real hard delete, not a soft one."
                    className="px-2 py-1 rounded text-xs font-medium bg-surface text-bad border border-bad hover:bg-bad/10 disabled:opacity-50"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
            <div className="text-[11px] text-muted mt-1">
              {u.last_login_at ? `last login ${new Date(u.last_login_at).toLocaleString()}` : "never logged in"}
            </div>
            {confirmDeleteId === u.id && (
              <div className="mt-2 border border-bad rounded p-2 bg-bad/5 text-xs flex items-center gap-3">
                <span className="text-text">
                  Permanently delete {u.display_name || u.email}? This cannot be undone.
                </span>
                <button
                  disabled={busyId === u.id}
                  onClick={() => deleteUser(u.id)}
                  className="px-2 py-1 rounded font-medium bg-bad text-white hover:bg-bad/80 disabled:opacity-50 shrink-0"
                >
                  {busyId === u.id ? "Deleting…" : "Confirm delete"}
                </button>
                <button onClick={() => setConfirmDeleteId(null)} className="text-muted hover:text-text shrink-0">
                  cancel
                </button>
              </div>
            )}
            {reinviteLink && reinviteLink.id === u.id && (
              <div className="mt-2">
                {reinviteLink.emailSent ? (
                  <div className="text-[11px] text-good mb-1">Emailed to the user.</div>
                ) : reinviteLink.emailError ? (
                  <div className="text-[11px] text-bad mb-1">Email failed ({reinviteLink.emailError}) — share this link manually.</div>
                ) : null}
                <input
                  readOnly
                  className="input"
                  value={reinviteLink.link}
                  onClick={(e) => (e.target as HTMLInputElement).select()}
                />
              </div>
            )}
          </div>
        ))}
      </div>
      <style jsx>{`
        .input {
          width: 100%;
          background: rgb(var(--c-canvas));
          border: 1px solid rgb(var(--c-border));
          border-radius: 6px;
          padding: 6px 8px;
          font-size: 0.8rem;
          color: rgb(var(--c-text));
        }
      `}</style>
    </div>
  );
}
