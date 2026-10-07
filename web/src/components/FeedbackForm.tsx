"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Card, CardTitle } from "@/components/Card";

export type FeedbackInfo = {
  repo: string;
  version: string;
  email_to: string | null;
  recent: { id: string; created_at: string; kind: string; title: string; channel: string; submitted_by: string | null }[];
};

const KINDS = [
  { key: "bug", label: "Bug", template: "bug_report.yml" },
  { key: "feature", label: "Feature request", template: "feature_request.yml" },
  { key: "feedback", label: "General feedback", template: "feedback.yml" },
] as const;
const CHANNEL_LABEL: Record<string, string> = { github: "opened on GitHub", email: "emailed", copied: "copied" };
const MAX_URL = 7000; // GitHub rejects very long query strings
const MAX_MAILTO = 1800; // mail apps and browsers truncate long mailto: links

function browserSummary(): string {
  const ua = navigator.userAgent;
  const b = /Edg\/([\d.]+)/.exec(ua) ? ["Edge", /Edg\/(\d+)/.exec(ua)?.[1]]
    : /Firefox\/(\d+)/.exec(ua) ? ["Firefox", /Firefox\/(\d+)/.exec(ua)?.[1]]
    : /Chrome\/(\d+)/.exec(ua) ? ["Chrome", /Chrome\/(\d+)/.exec(ua)?.[1]]
    : /Version\/(\d+).*Safari/.exec(ua) ? ["Safari", /Version\/(\d+)/.exec(ua)?.[1]] : ["Unknown browser", ""];
  const os = /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /Linux/.test(ua) ? "Linux" : /iPhone|iPad/.test(ua) ? "iOS" : "unknown OS";
  return `${b[0]} ${b[1] ?? ""} on ${os}`.replace("  ", " ");
}

export default function FeedbackForm({ info, serverFields, from }: { info: FeedbackInfo; serverFields: { label: string; value: string }[]; from: string }) {
  const router = useRouter();
  const [kind, setKind] = useState<(typeof KINDS)[number]>(KINDS[0]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [withDiag, setWithDiag] = useState(true);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const diagText = useMemo(() => {
    const rows = [...serverFields];
    if (from) rows.splice(1, 0, { label: "Page", value: from });
    if (typeof navigator !== "undefined") rows.splice(from ? 2 : 1, 0, { label: "Browser", value: browserSummary() });
    return rows.map((r) => `${r.label}: ${r.value}`).join("\n");
  }, [serverFields, from]);

  const valid = title.trim().length > 0 && description.trim().length > 0;
  const need = () => { setNotice({ ok: false, text: "Add a title and a description first." }); };

  async function post(path: string, body: unknown, method = "POST") {
    const res = await fetch(`/api/feedback/${path}`, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }
  const record = (channel: "github" | "email" | "copied") => post("submissions", { kind: kind.key, title: title.trim(), channel, diagnostics_included: withDiag });

  async function openGithub() {
    if (!valid) return need();
    const mk = (desc: string) => {
      const q = new URLSearchParams({ template: kind.template, title: title.trim(), version: info.version, description: desc });
      if (withDiag) q.set("diagnostics", diagText);
      return `https://github.com/${info.repo}/issues/new?${q.toString()}`;
    };
    let desc = description.trim();
    let url = mk(desc);
    let shortened = false;
    while (url.length > MAX_URL && desc.length > 200) {
      desc = desc.slice(0, Math.floor(desc.length * 0.8));
      url = mk(desc);
      shortened = true;
    }
    window.open(url, "_blank", "noopener,noreferrer");
    try { await record("github"); router.refresh(); } catch { /* the record is a convenience */ }
    setNotice({ ok: true, text: shortened ? "GitHub opened, but your description was too long for a link and was shortened. Paste the rest there, or use Copy as text." : "GitHub opened in a new tab. Review the issue there and submit it." });
  }

  async function openEmail() {
    if (!valid || !info.email_to) return need();
    const subject = `[PyXie ${kind.key}] ${title.trim()}`;
    const mk = (desc: string) => `mailto:${info.email_to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(`${desc}${withDiag ? `\n\n--- Diagnostics ---\n${diagText}` : ""}\n\nPyXie version: ${info.version}\n`)}`;
    let desc = description.trim();
    let url = mk(desc);
    let shortened = false;
    while (url.length > MAX_MAILTO && desc.length > 200) {
      desc = desc.slice(0, Math.floor(desc.length * 0.8));
      url = mk(desc);
      shortened = true;
    }
    window.location.href = url;
    try { await record("email"); router.refresh(); } catch { /* the record is a convenience */ }
    setNotice({ ok: true, text: shortened ? "Your mail app should open with the message filled in. It was too long for a link and was shortened; add the rest there, or use Copy as text." : "Your mail app should open with the message filled in. Review it and send it from there." });
  }

  async function copyText() {
    if (!valid) return need();
    const text = `${kind.label}: ${title.trim()}\nPyXie version: ${info.version}\n\n${description.trim()}${withDiag ? `\n\n--- Diagnostics ---\n${diagText}` : ""}\n`;
    try { await navigator.clipboard.writeText(text); } catch { setNotice({ ok: false, text: "Your browser blocked copying. Select the text and copy it by hand." }); return; }
    try { await record("copied"); router.refresh(); } catch { /* ignore */ }
    setNotice({ ok: true, text: `Copied. Post it at github.com/${info.repo}/issues or email it to the maintainers.` });
  }

  const chip = (active: boolean) => `px-3 py-1 rounded border text-sm ${active ? "border-accent text-accent bg-accent/10" : "border-border text-muted"}`;
  const btn = "px-3 py-1.5 rounded text-sm border border-border bg-surface2 text-text hover:bg-surface2/70";

  return (
    <div className="space-y-4 max-w-3xl">
      <Card>
        <div className="flex gap-2 mb-3 flex-wrap">
          {KINDS.map((k) => <button key={k.key} type="button" className={chip(kind.key === k.key)} onClick={() => setKind(k)}>{k.label}</button>)}
        </div>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="A short summary" aria-label="Title"
          className="w-full mb-2 px-3 py-2 rounded border border-border bg-surface text-sm text-text" />
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={6} maxLength={10000} aria-label="Description"
          placeholder={kind.key === "bug" ? "What happened, what you expected, and the steps to repeat it." : "What would you like, and what problem does it solve?"}
          className="w-full px-3 py-2 rounded border border-border bg-surface text-sm text-text" />
        <p className="text-xs text-muted mt-1 mb-3">Do not paste passwords, tokens or keys. PyXie never adds them for you. GitHub issues in the feedback repository are public.</p>
        <label className="flex items-center gap-2 text-sm text-text mb-2">
          <input type="checkbox" checked={withDiag} onChange={(e) => setWithDiag(e.target.checked)} /> Include diagnostics
        </label>
        {withDiag && (
          <>
            <pre className="text-xs text-muted bg-surface2 border border-border rounded px-3 py-2 whitespace-pre-wrap">{diagText}</pre>
            <p className="text-xs text-muted mt-1 mb-3">This is everything that will be included. Not included: hostnames, IP addresses, VM names, credentials, logs.</p>
          </>
        )}
        <div className="flex gap-2 flex-wrap items-center">
          <button type="button" onClick={openGithub} className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10">Open on GitHub</button>
          {info.email_to && <button type="button" onClick={openEmail} className={btn}>Send by email</button>}
          <button type="button" onClick={copyText} className={btn}>Copy as text</button>
        </div>
        <p className="text-xs text-muted mt-2">
          GitHub opens with this filled in and you review it there before submitting; it needs a GitHub account.
          {info.email_to ? " No GitHub account? Send by email opens your own mail app with the message filled in." : " No GitHub account? Copy as text and send it to the maintainers."}
        </p>
        {notice && <p className={`text-sm mt-2 ${notice.ok ? "text-text" : "text-danger"}`} role="status">{notice.text}</p>}
      </Card>

      <Card>
        <CardTitle>Sent from this server</CardTitle>
        {info.recent.length === 0 ? (
          <p className="text-sm text-muted">Nothing yet. This is a local record of what was opened, emailed or copied here; it does not keep the descriptions.</p>
        ) : (
          <div className="divide-y divide-border text-sm">
            {info.recent.map((r) => (
              <div key={r.id} className="py-1.5 flex justify-between gap-3">
                <span className="text-text">{r.kind}: {r.title}</span>
                <span className="text-muted text-xs">{CHANNEL_LABEL[r.channel] ?? r.channel}, {new Date(r.created_at).toLocaleDateString()}, {r.submitted_by}</span>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-muted mt-3">
          <a className="text-accent hover:underline" href={`https://github.com/${info.repo}/blob/main/KNOWN_ISSUES.md`} target="_blank" rel="noopener noreferrer">Known issues and roadmap</a>
          {" · "}
          <a className="text-accent hover:underline" href={`https://github.com/${info.repo}/issues`} target="_blank" rel="noopener noreferrer">Browse open issues</a>
        </p>
      </Card>
    </div>
  );
}
