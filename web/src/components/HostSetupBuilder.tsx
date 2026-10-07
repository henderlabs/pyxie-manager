"use client";

import { useMemo, useState } from "react";
import { useMe } from "@/lib/useMe";

type Target = { id: string; name: string };
type Link = { path: string; sha256: string; expires_in: number; kit_version: string; wrapper_version: string };
type ConsoleOpt = "none" | "maintenance" | "separate";

const MAINT_PRIVS = "Datastore.Allocate,Datastore.AllocateSpace,Sys.Audit,Sys.Modify,Sys.PowerMgmt,VM.Audit,VM.Config.CPU,VM.Config.Disk,VM.Config.Memory,VM.Migrate,VM.PowerMgmt";

function pveScript(o: { roUser: string; adminUser: string; consoleUser: string; inventory: boolean; maintenance: boolean; console: ConsoleOpt }): string {
  const L: string[] = [
    "#!/bin/bash",
    "# PyXie Manager: Proxmox VE service accounts, roles and API tokens.",
    "# Run ONCE, as root, on any node of the cluster. Safe to re-run: existing users, roles and tokens are left alone.",
    "# Token secrets are shown by PVE exactly once, when a token is created: copy each into PyXie (Platform > Credentials).",
    "set -euo pipefail",
    'command -v pveum >/dev/null || { echo "pveum not found: run this on a Proxmox VE node" >&2; exit 1; }',
    "ensure_user() { # ensure_user <user> <comment>",
    '  pveum user add "$1" --comment "$2" 2>/dev/null || echo "user $1 already exists"',
    "}",
    'have_token() { pveum user token list "$1" --output-format json 2>/dev/null | grep -Eq "\\"tokenid\\": *\\"$2\\""; }',
    "add_token() { # add_token <user> <token-id> <comment>",
    '  if have_token "$1" "$2"; then echo "token $1!$2 already exists"; else pveum user token add "$1" "$2" --privsep 1 --comment "$3"; fi',
    "}",
    "grant() { # grant <path> <role> <user> <token-id>: the user AND the token need the grant (privilege separation)",
    '  pveum acl modify "$1" --roles "$2" --users "$3"',
    '  pveum acl modify "$1" --roles "$2" --tokens "$3!$4"',
    "}",
  ];
  const users: string[] = [];
  if (o.inventory) {
    users.push(o.roUser);
    L.push("", "# Read-only account: inventory token (built-in PVEAuditor role)", `ensure_user '${o.roUser}' "PyXie read-only (inventory)"`, `add_token '${o.roUser}' inventory "PyXie inventory (read-only)"`, `grant / PVEAuditor '${o.roUser}' inventory`);
  }
  if (o.maintenance) {
    users.push(o.adminUser);
    const privs = MAINT_PRIVS + (o.console === "maintenance" ? ",VM.Console" : "");
    L.push(
      "",
      `# Admin account: maintenance token (migrations, power, host reboots${o.console === "maintenance" ? ", embedded console" : ""})`,
      `ensure_user '${o.adminUser}' "PyXie admin (maintenance)"`,
      `pveum role add PyXieAdmin --privs "${privs}" 2>/dev/null || pveum role modify PyXieAdmin --privs "${privs}"`,
      `add_token '${o.adminUser}' maintenance "PyXie maintenance (admin)"`,
      `grant / PyXieAdmin '${o.adminUser}' maintenance`,
    );
  }
  if (o.console === "separate") {
    users.push(o.consoleUser);
    L.push("", "# Console account: its own token, VM.Console only (built-in role PVEVMConsole)", `ensure_user '${o.consoleUser}' "PyXie embedded console"`, `add_token '${o.consoleUser}' console "PyXie embedded console"`, `grant /vms PVEVMConsole '${o.consoleUser}' console`);
  }
  L.push("", 'echo ""', 'echo "Done. Tokens (secrets are NOT shown again):"');
  for (const u of users) L.push(`pveum user token list '${u}'`);
  return L.join("\n") + "\n";
}

function hostScript(o: { origin: string; link: Link; mode: "install" | "uninstall"; insecure: boolean }): string {
  const url = `${o.origin}${o.link.path}`;
  return [
    "#!/bin/bash",
    `# PyXie Manager: ${o.mode === "install" ? "install or upgrade" : "remove"} the host-maintenance wrapper on THIS node (kit ${o.link.kit_version}, wrapper ${o.link.wrapper_version}).`,
    "# Run as root on EVERY Proxmox node you want PyXie to patch and reboot. Safe to re-run.",
    `# The download link expires in ${Math.round(o.link.expires_in / 60)} minutes. The checksum below comes from PyXie: the script refuses to run if the file differs.`,
    "set -euo pipefail",
    '[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }',
    'F="$(mktemp /tmp/pyxie-host-kit.XXXXXX)"',
    "trap 'rm -f \"$F\"' EXIT",
    `curl -fsS${o.insecure ? "k" : ""} -o "$F" '${url}'`,
    `echo "${o.link.sha256}  $F" | sha256sum -c -`,
    `bash "$F"${o.mode === "uninstall" ? " --uninstall" : ""}`,
    "",
  ].join("\n");
}

function download(name: string, text: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/x-shellscript" }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

function ScriptBox({ title, hint, text, filename }: { title: string; hint: string; text: string; filename: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="border border-border rounded p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm font-medium text-text">{title}</div>
        <div className="ml-auto flex gap-2">
          <button type="button" className="px-2.5 py-1 rounded border border-border text-sm hover:bg-surface2"
            onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* blocked */ } }}>
            {copied ? "Copied" : "Copy"}
          </button>
          <button type="button" className="px-2.5 py-1 rounded border border-border text-sm hover:bg-surface2" onClick={() => download(filename, text)}>Download</button>
        </div>
      </div>
      <p className="text-xs text-muted">{hint}</p>
      <pre className="text-xs font-mono bg-canvas/60 border border-border rounded p-2 max-h-72 overflow-auto whitespace-pre">{text}</pre>
    </div>
  );
}

function UserField({ label, value, onChange, ok }: { label: string; value: string; onChange: (v: string) => void; ok: boolean }) {
  return (
    <label className="block text-xs text-muted space-y-1 pl-6">
      <span>{label}</span>
      <input className="input" value={value} onChange={(e) => onChange(e.target.value)} />
      {!ok && <span className="text-bad">Use the form name@pve.</span>}
    </label>
  );
}

function Check({ checked, onChange, label, sub }: { checked: boolean; onChange: (v: boolean) => void; label: string; sub?: string }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span><span className="text-text">{label}</span>{sub && <span className="block text-xs text-muted">{sub}</span>}</span>
    </label>
  );
}

type Method = "paste" | "file" | "tar";

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative">
      <pre className="text-xs font-mono bg-canvas/60 border border-border rounded p-2 pr-16 overflow-x-auto whitespace-pre">{text}</pre>
      <button type="button" className="absolute top-1.5 right-1.5 px-2 py-0.5 rounded border border-border text-[11px] hover:bg-surface2"
        onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* blocked */ } }}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/** What to do after the script exists: three ways to get it onto a node, then check it and pin the host key. */
function AfterGenerate({ targetId, sha256, mode }: { targetId: string; sha256: string; mode: "install" | "uninstall" }) {
  const [method, setMethod] = useState<Method>("paste");
  const [node, setNode] = useState("");
  const n = node.trim() || "NODE-IP";
  const flag = mode === "uninstall" ? " --uninstall" : "";
  const verify = "sudo -u pyxie-hostmaint sudo /usr/local/sbin/pyxie-maint version";
  const fileCmds = [
    "# 1. On your computer, in the folder the file downloaded to: copy it to the node",
    `scp pyxie-host-kit.sh root@${n}:/root/`,
    "# 2. Log in to the node",
    `ssh root@${n}`,
    "# 3. On the node: check the file is the one PyXie made. The line below must equal the checksum shown above",
    "sha256sum pyxie-host-kit.sh",
    `#    expected: ${sha256}`,
    `# 4. Run it${mode === "install" ? " (install or upgrade; add --check first to look without changing anything)" : " (removes the PyXie identity, wrapper and sudoers entry)"}`,
    `bash pyxie-host-kit.sh${flag}`,
  ].join("\n");
  const tarCmds = [
    "# 1. On your computer: copy the downloaded kit to the node",
    `scp pyxie-hostmaint-kit-*.tar.gz root@${n}:/root/`,
    "# 2. Log in, unpack and install (as root; the public key is already inside the kit)",
    `ssh root@${n}`,
    "tar xzf pyxie-hostmaint-kit-*.tar.gz",
    "cd pyxie-hostmaint-kit",
    mode === "install" ? "bash install.sh" : "bash uninstall.sh",
  ].join("\n");
  const pasteCmds = [
    "# On the node, as root: paste the script above into a file, then run it",
    "nano pyxie-host-setup.sh        # paste, then Ctrl-O, Enter, Ctrl-X",
    "bash pyxie-host-setup.sh",
  ].join("\n");

  return (
    <div className="border border-border rounded p-3 space-y-3">
      <div className="text-sm font-medium text-text">Getting it onto the node</div>
      <div className="flex gap-4 flex-wrap text-sm">
        {([["paste", "Paste on the node (simplest)"], ["file", "Copy the installer file"], ["tar", "Copy the classic .tar.gz kit"]] as [Method, string][]).map(([v, label]) => (
          <label key={v} className="flex items-center gap-1.5"><input type="radio" name="host-method" checked={method === v} onChange={() => setMethod(v)} />{label}</label>
        ))}
      </div>
      {method !== "paste" && (
        <label className="block text-xs text-muted space-y-1 max-w-xs">
          <span>Node address (fills in the commands)</span>
          <input className="input" placeholder="192.168.1.101" value={node} onChange={(e) => setNode(e.target.value)} />
        </label>
      )}
      {method === "paste" && <CodeBlock text={pasteCmds} />}
      {method === "file" && (
        <div className="space-y-2">
          <a href={`/api/pve-targets/${targetId}/host-kit.sh`} className="inline-block px-2.5 py-1 rounded border border-border text-sm hover:bg-surface2">Download pyxie-host-kit.sh</a>
          <p className="text-xs text-muted">One self-extracting file: nothing to unzip. Checksum: <code className="break-all">{sha256}</code></p>
          <CodeBlock text={fileCmds} />
        </div>
      )}
      {method === "tar" && (
        <div className="space-y-2">
          <a href={`/api/pve-targets/${targetId}/host-maintenance-kit`} className="inline-block px-2.5 py-1 rounded border border-border text-sm hover:bg-surface2">Download the .tar.gz kit</a>
          <p className="text-xs text-muted">The original format: the scripts plus this PyXie&apos;s public key, unpacked and installed by hand.</p>
          <CodeBlock text={tarCmds} />
        </div>
      )}
      {mode === "install" && (
        <div className="space-y-2">
          <div className="text-xs uppercase tracking-wide text-muted">Then, on the node: check it</div>
          <CodeBlock text={verify} />
          <p className="text-xs text-muted">It should print <code>wrapper_version</code> 1.1.0 or newer and a <code>log</code> capability.</p>
          <div className="text-xs uppercase tracking-wide text-muted">Then, back in PyXie: pin the node&apos;s SSH host key</div>
          <ol className="list-decimal pl-5 text-xs text-muted space-y-0.5">
            <li>Open <a className="text-accent hover:underline" href="/platform/credentials">Credentials</a>, find this cluster&apos;s Host Maintenance section and the node&apos;s row, and click the probe button. PyXie shows the fingerprint it sees.</li>
            <li>On the node, run <code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> and compare the two fingerprints.</li>
            <li>Only if they match, click Confirm match, Pin. That is what stops PyXie trusting the wrong machine.</li>
          </ol>
        </div>
      )}
    </div>
  );
}

/** Tick what you need, get scripts to copy onto a node. Nothing here runs anything: PyXie never provisions hosts itself. */
export default function HostSetupBuilder({ targets }: { targets: Target[] }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [targetId, setTargetId] = useState(targets[0]?.id ?? "");
  const [roUser, setRoUser] = useState("pyxie-ro@pve");
  const [adminUser, setAdminUser] = useState("pyxie-admin@pve");
  const [consoleUser, setConsoleUser] = useState("pyxie-console@pve");
  const [inventory, setInventory] = useState(true);
  const [maintenance, setMaintenance] = useState(false);
  const [consoleOpt, setConsoleOpt] = useState<ConsoleOpt>("none");
  const noTarget = targets.length === 0;
  const [hostMode, setHostMode] = useState<"none" | "install" | "uninstall">(targets.length === 0 ? "none" : "install");
  const [insecure, setInsecure] = useState(false);
  const [link, setLink] = useState<Link | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [needKey, setNeedKey] = useState(false);

  const okName = (u: string) => /^[A-Za-z0-9._-]+@pve$/.test(u);
  const effConsole: ConsoleOpt = consoleOpt === "maintenance" && !maintenance ? "none" : consoleOpt;
  const anyPve = inventory || maintenance || effConsole === "separate";
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const namesOk = (!inventory || okName(roUser)) && (!maintenance || okName(adminUser)) && (effConsole !== "separate" || okName(consoleUser));
  const pve = useMemo(
    () => (namesOk && anyPve ? pveScript({ roUser, adminUser, consoleUser, inventory, maintenance, console: effConsole }) : ""),
    [roUser, adminUser, consoleUser, namesOk, inventory, maintenance, effConsole, anyPve],
  );
  const touch = () => setGenerated(false);

  async function generate() {
    setError(null);
    setBusy(true);
    try {
      if (hostMode !== "none") {
        const r = await fetch(`/api/pve-targets/${targetId}/host-kit/link`, { method: "POST" });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          if (r.status === 409 && /key pair|host-maintenance credential/i.test(d.error || "")) setNeedKey(true);
          throw new Error(r.status === 409 ? "The host key pair has not been generated yet." : d.error || `Request failed (${r.status})`);
        }
        setNeedKey(false);
        setLink(d);
      } else {
        setLink(null);
      }
      setGenerated(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function generateKeyThenRetry() {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`/api/pve-targets/${targetId}/host-maintenance-credential/generate`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
      setNeedKey(false);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      return;
    }
    setBusy(false);
    await generate();
  }

  if (!isAdmin) return <p className="text-sm text-muted">Admin accounts can generate host scripts.</p>;

  return (
    <div className="space-y-4">
      {targets.length > 1 && (
        <label className="block text-xs text-muted space-y-1 max-w-xs">
          <span>PVE target</span>
          <select className="input" value={targetId} onChange={(e) => { setTargetId(e.target.value); touch(); }}>
            {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <div className="text-xs uppercase tracking-wide text-muted">1. Proxmox account, once per cluster</div>
          <Check checked={inventory} onChange={(v) => { setInventory(v); touch(); }} label="Inventory token (read-only)" sub="Built-in PVEAuditor role. Discovery and monitoring." />
          {inventory && <UserField label="Read-only user" value={roUser} onChange={(v) => { setRoUser(v); touch(); }} ok={okName(roUser)} />}
          <Check checked={maintenance} onChange={(v) => { setMaintenance(v); touch(); }} label="Maintenance (Admin) token (write)" sub="Role PyXieAdmin: migrations, power, reboots. Still gated by PyXie's own Settings switch." />
          {maintenance && <UserField label="Admin user" value={adminUser} onChange={(v) => { setAdminUser(v); touch(); }} ok={okName(adminUser)} />}
          <div className="pl-6 space-y-1">
            <div className="text-xs text-muted">Embedded VM console</div>
            {([
              ["none", "Not needed"],
              ["maintenance", "Add VM.Console to the admin role"],
              ["separate", "Separate console token (VM.Console only, smaller blast radius)"],
            ] as [ConsoleOpt, string][]).map(([v, label]) => (
              <label key={v} className={`flex items-center gap-2 text-sm ${v === "maintenance" && !maintenance ? "opacity-50" : ""}`}>
                <input type="radio" name="console-opt" checked={effConsole === v} disabled={v === "maintenance" && !maintenance}
                  onChange={() => { setConsoleOpt(v); touch(); }} />
                {label}
              </label>
            ))}
            {effConsole === "separate" && <UserField label="Console user" value={consoleUser} onChange={(v) => { setConsoleUser(v); touch(); }} ok={okName(consoleUser)} />}
          </div>
        </div>

        <div className="space-y-2">
          <div className="text-xs uppercase tracking-wide text-muted">2. On each node</div>
          {noTarget && <p className="text-xs text-warn">The host script needs a connected PVE target (step 3), so it is not available yet. Do step 2 first, then come back for this part.</p>}
          {([
            ["install", "Install or upgrade the host wrapper", "Lets PyXie apply updates and reboot this node, with live output. Running it again upgrades."],
            ["uninstall", "Remove the host wrapper", "Removes the PyXie SSH identity, wrapper and sudoers entry from this node."],
            ["none", "Skip this part", ""],
          ] as ["install" | "uninstall" | "none", string, string][]).map(([v, label, sub]) => (
            <label key={v} className="flex items-start gap-2 text-sm">
              <input type="radio" name="host-opt" className="mt-1" checked={hostMode === v} disabled={noTarget && v !== "none"} onChange={() => { setHostMode(v); touch(); }} />
              <span><span className="text-text">{label}</span>{sub && <span className="block text-xs text-muted">{sub}</span>}</span>
            </label>
          ))}
          {hostMode !== "none" && (
            <Check checked={insecure} onChange={(v) => { setInsecure(v); touch(); }} label="This node does not trust PyXie's certificate"
              sub="Adds curl -k. Safe here: the script verifies the downloaded file against the checksum PyXie gives it." />
          )}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button type="button" disabled={busy || (!pve && hostMode === "none")} onClick={generate}
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50">
          {busy ? "Generating…" : "Generate scripts"}
        </button>
        {error && <span className="text-sm text-bad">{error}</span>}
        {needKey && (
          <button type="button" onClick={generateKeyThenRetry} disabled={busy}
            className="px-3 py-1.5 rounded text-sm border border-warn hover:bg-warn/10 disabled:opacity-50">Generate the host key pair now</button>
        )}
      </div>

      {generated && (
        <div className="space-y-3">
          {pve && <ScriptBox title="Script 1: Proxmox accounts (run once, as root, on any node)" hint="Copy it to a node and run it with bash. Copy each token secret it prints into PyXie; PVE shows it only once." text={pve} filename="pyxie-pve-setup.sh" />}
          {hostMode !== "none" && link && (
            <ScriptBox
              title={`Script ${pve ? 2 : 1}: host wrapper (${hostMode === "install" ? "install or upgrade" : "remove"}, run as root on every node)`}
              hint={`The link expires in ${Math.round(link.expires_in / 60)} minutes (generate again after that). Expected checksum ${link.sha256.slice(0, 16)}…`}
              text={hostScript({ origin, link, mode: hostMode, insecure })} filename="pyxie-host-setup.sh" />
          )}
          {hostMode !== "none" && link && <AfterGenerate targetId={targetId} sha256={link.sha256} mode={hostMode} />}
          <p className="text-xs text-muted">
            After the wrapper is on a node, pin its SSH host key on the Credentials page. Nothing on this page runs anything on your hosts; you review and run the scripts yourself.
          </p>
        </div>
      )}
    </div>
  );
}
