"use client";

import { useState } from "react";
import type { Me } from "@/lib/api";

export default function ProfileForm({ me }: { me: Me }) {
  const [displayName, setDisplayName] = useState(me.display_name || "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const wantsPasswordChange = newPassword.length > 0 || confirmPassword.length > 0 || currentPassword.length > 0;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (wantsPasswordChange) {
      if (newPassword !== confirmPassword) {
        setError("New password and confirmation don't match");
        return;
      }
      if (newPassword.length < 10) {
        setError("New password must be at least 10 characters");
        return;
      }
      if (!currentPassword) {
        setError("Enter your current password to set a new one");
        return;
      }
    }

    setPending(true);
    try {
      const body: Record<string, string> = {};
      if (displayName.trim() !== (me.display_name || "")) body.display_name = displayName.trim();
      if (wantsPasswordChange) {
        body.current_password = currentPassword;
        body.new_password = newPassword;
      }
      if (Object.keys(body).length === 0) {
        setSuccess("Nothing to save");
        return;
      }
      const res = await fetch("/api/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Save failed (${res.status})`);
        return;
      }
      setSuccess("Saved");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-5 max-w-md">
      <div>
        <div className="text-xs text-muted mb-1">Email</div>
        <div className="text-sm text-text">{me.email}</div>
        <div className="text-[11px] text-muted mt-0.5">Email can't be changed here -- ask an admin.</div>
      </div>

      <div>
        <label className="text-xs text-muted mb-1 block">Display name</label>
        <input
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="input"
        />
      </div>

      <div className="border-t border-border pt-4">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">Change password</div>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted mb-1 block">Current password</label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="input"
              autoComplete="current-password"
            />
          </div>
          <div>
            <label className="text-xs text-muted mb-1 block">New password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="input"
              autoComplete="new-password"
              placeholder="At least 10 characters"
            />
          </div>
          <div>
            <label className="text-xs text-muted mb-1 block">Confirm new password</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="input"
              autoComplete="new-password"
            />
          </div>
        </div>
      </div>

      {error && <div className="text-xs text-bad">{error}</div>}
      {success && <div className="text-xs text-good">{success}</div>}

      <button
        type="submit"
        disabled={pending}
        className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
      >
        {pending ? "Saving…" : "Save changes"}
      </button>

      <style jsx>{`
        .input {
          width: 100%;
          background: rgb(var(--c-canvas));
          border: 1px solid rgb(var(--c-border));
          border-radius: 6px;
          padding: 6px 8px;
          font-size: 0.85rem;
          color: rgb(var(--c-text));
        }
      `}</style>
    </form>
  );
}
