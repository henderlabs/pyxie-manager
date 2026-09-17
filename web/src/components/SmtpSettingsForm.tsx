"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { AppSettings } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function SmtpSettingsForm({ initial }: { initial: AppSettings }) {
  const router = useRouter();
  const [form, setForm] = useState(initial);
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [testPending, setTestPending] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      const body: Record<string, unknown> = {
        smtp_enabled: form.smtp_enabled,
        smtp_host: form.smtp_host,
        smtp_port: form.smtp_port,
        smtp_username: form.smtp_username,
        smtp_from_address: form.smtp_from_address,
        smtp_use_tls: form.smtp_use_tls,
        notification_recipient: form.notification_recipient,
      };
      if (password) body.smtp_password = password;
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setForm(data);
      setPassword("");
      setSaved(true);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function sendTest() {
    setTestPending(true);
    setTestResult(null);
    try {
      const res = await fetch("/api/settings/test-email", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === "failed") {
        setTestResult(`Failed: ${data.error || `request failed (${res.status})`}`);
      } else {
        setTestResult(`Sent to ${form.notification_recipient}.`);
      }
    } catch (e) {
      setTestResult(`Failed: ${(e as Error).message}`);
    } finally {
      setTestPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 max-w-lg">
      <label className="flex items-center gap-2 text-sm font-medium text-text">
        <input
          type="checkbox"
          disabled={!isAdmin}
          checked={form.smtp_enabled}
          onChange={(e) => setForm({ ...form, smtp_enabled: e.target.checked })}
        />
        Enable email notifications
      </label>
      <p className="text-[11px] text-muted normal-case -mt-2">
        Configure an outgoing SMTP server so PyXie can email alerts and notifications. Nothing is sent unless
        this is checked and a notification recipient is set below.
      </p>
      <Field label="SMTP host">
        <input
          className="input"
          disabled={!isAdmin}
          value={form.smtp_host ?? ""}
          onChange={(e) => setForm({ ...form, smtp_host: e.target.value })}
          placeholder="smtp.example.com"
        />
      </Field>
      <Field label="Port">
        <input
          type="number"
          className="input"
          disabled={!isAdmin}
          value={form.smtp_port}
          onChange={(e) => setForm({ ...form, smtp_port: Number(e.target.value) })}
        />
      </Field>
      <label className="flex items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          disabled={!isAdmin}
          checked={form.smtp_use_tls}
          onChange={(e) => setForm({ ...form, smtp_use_tls: e.target.checked })}
        />
        Use STARTTLS
      </label>
      <Field label="Username">
        <input
          className="input"
          disabled={!isAdmin}
          value={form.smtp_username ?? ""}
          onChange={(e) => setForm({ ...form, smtp_username: e.target.value })}
        />
      </Field>
      <Field label={form.smtp_password_set ? "Password (set — leave blank to keep current)" : "Password"}>
        <input
          type="password"
          className="input"
          disabled={!isAdmin}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={form.smtp_password_set ? "••••••••" : ""}
        />
      </Field>
      <Field label="From address">
        <input
          className="input"
          disabled={!isAdmin}
          value={form.smtp_from_address ?? ""}
          onChange={(e) => setForm({ ...form, smtp_from_address: e.target.value })}
          placeholder="pyxie@example.com"
        />
      </Field>
      <Field label="Notification recipient">
        <input
          className="input"
          disabled={!isAdmin}
          value={form.notification_recipient ?? ""}
          onChange={(e) => setForm({ ...form, notification_recipient: e.target.value })}
          placeholder="you@example.com"
        />
      </Field>
      <p className="text-[11px] text-muted normal-case -mt-2">
        Where test emails and future alert notifications are sent. Comma-separate multiple addresses.
      </p>
      {error && <div className="text-xs text-bad">{error}</div>}
      {saved && !error && <div className="text-xs text-good">Saved.</div>}
      {isAdmin && (
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={pending}
            className="px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save email settings"}
          </button>
          <button
            type="button"
            onClick={sendTest}
            disabled={testPending || !form.notification_recipient}
            className="px-3 py-1.5 rounded text-sm font-medium bg-surface2 text-text border border-border hover:bg-surface2/70 disabled:opacity-50"
          >
            {testPending ? "Sending…" : "Send test email"}
          </button>
        </div>
      )}
      {testResult && (
        <div className={`text-xs ${testResult.startsWith("Failed") ? "text-bad" : "text-good"}`}>{testResult}</div>
      )}
      <p className="text-[11px] text-muted normal-case">
        The test uses your last <em>saved</em> settings, not unsaved edits above — save first if you just made
        changes.
      </p>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs text-muted space-y-1">
      <span>{label}</span>
      {children}
    </label>
  );
}
