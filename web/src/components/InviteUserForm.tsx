"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function InviteUserForm() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [form, setForm] = useState({ email: "", display_name: "", is_admin: false });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setInviteLink(null);
    try {
      const res = await fetch("/api/auth/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, display_name: form.display_name || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setInviteLink(`${window.location.origin}/accept-invite?token=${data.invite_token}`);
      setForm({ email: "", display_name: "", is_admin: false });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => {
          setOpen(true);
          setInviteLink(null);
        }}
        className="px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10"
      >
        + Invite User
      </button>
    );
  }

  if (inviteLink) {
    return (
      <div className="bg-surface2 border border-border rounded-lg p-4 space-y-3 max-w-lg">
        <div className="text-sm font-semibold text-text">Invite created</div>
        <div className="text-xs text-muted">
          Send this link to the new user however you normally would (Slack, text, in person). It expires in 7 days
          and works once. There is no automatic email — PyXie has no mail configured.
        </div>
        <input
          readOnly
          className="input"
          value={inviteLink}
          onClick={(e) => (e.target as HTMLInputElement).select()}
        />
        <button
          onClick={() => {
            setOpen(false);
            setInviteLink(null);
          }}
          className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-muted border border-border"
        >
          Done
        </button>
        <style jsx>{`
          .input {
            width: 100%;
            background: #0b0e14;
            border: 1px solid #232a38;
            border-radius: 6px;
            padding: 6px 8px;
            font-size: 0.8rem;
            color: #e6e9ef;
          }
        `}</style>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="bg-surface2 border border-border rounded-lg p-4 space-y-3 max-w-lg">
      <div className="text-sm font-semibold text-text">Invite User</div>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-muted space-y-1">
          <span>Email</span>
          <input
            required
            type="email"
            className="input"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
        </label>
        <label className="block text-xs text-muted space-y-1">
          <span>Display name (optional)</span>
          <input
            className="input"
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
          />
        </label>
      </div>
      <label className="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" checked={form.is_admin} onChange={(e) => setForm({ ...form, is_admin: e.target.checked })} />
        Admin (full access, including managing other users). Leave unchecked for read-only Viewer access.
      </label>
      {error && <div className="text-xs text-bad">{error}</div>}
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={pending}
          className="px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Creating…" : "Create invite"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="px-3 py-1.5 rounded text-sm font-medium bg-surface text-muted border border-border"
        >
          Cancel
        </button>
      </div>
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
