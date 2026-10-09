"use client";

import { useMemo, useState } from "react";
import { useMe } from "@/lib/useMe";
import { hostScript, pveScript, TOKEN_FILE } from "@/lib/hostScripts";
import type { ConsoleOpt, Link } from "@/lib/hostScripts";

type Target = { id: string; name: string };
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

const INPUT = "w-full max-w-xs bg-canvas border border-border rounded px-2 py-1.5 text-sm text-text font-mono";

/** A named, editable account field with a visible box. The default is what the guide and docs use; change it if you want another name. */
function UserField({ label, value, onChange, ok, def }: { label: string; value: string; onChange: (v: string) => void; ok: boolean; def: string }) {
  return (
    <label className="block text-xs text-muted space-y-1 pl-6">
      <span className="text-text">{label}</span>
      <input className={`${INPUT} ${ok ? "" : "border-bad"}`} value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
      <span className="block">{ok ? `Editable. Default: ${def}. Any name works; it is only a label in Proxmox.` : <span className="text-bad">Use the form name@pve.</span>}</span>
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
    "# On the node, as root: paste the script above straight into the shell (it is paste-safe),",
    "# or save it to a file and run it:",
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
          <input className={INPUT} placeholder="192.168.1.101" value={node} onChange={(e) => setNode(e.target.value)} />
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

const SETUP_LINK = (
  <a href="#setup-guide" className="text-accent hover:underline">Back to the Setup guide</a>
);

/** The first card: Proxmox accounts, tokens and roles. Once per cluster. Nothing is ticked until you choose. */
export function PveAccountsBuilder() {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [roUser, setRoUser] = useState("pyxie-ro@pve");
  const [adminUser, setAdminUser] = useState("pyxie-admin@pve");
  const [consoleUser, setConsoleUser] = useState("pyxie-console@pve");
  const [inventory, setInventory] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const [consoleOpt, setConsoleOpt] = useState<ConsoleOpt>("none");
  const [generated, setGenerated] = useState(false);

  const okName = (u: string) => /^[A-Za-z0-9._-]+@pve$/.test(u);
  const effConsole: ConsoleOpt = consoleOpt === "maintenance" && !maintenance ? "none" : consoleOpt;
  const anyPve = inventory || maintenance || effConsole === "separate";
  const namesOk = (!inventory || okName(roUser)) && (!maintenance || okName(adminUser)) && (effConsole !== "separate" || okName(consoleUser));
  const pve = useMemo(
    () => (namesOk && anyPve ? pveScript({ roUser, adminUser, consoleUser, inventory, maintenance, console: effConsole }) : ""),
    [roUser, adminUser, consoleUser, namesOk, inventory, maintenance, effConsole, anyPve],
  );
  const touch = () => setGenerated(false);

  if (!isAdmin) return <p className="text-sm text-muted">Admin accounts can generate setup scripts.</p>;

  const rows: [string, string, string, string][] = [];
  if (inventory) rows.push(["inventory", roUser, "inventory", "Step 3, Connect your cluster (PVE target form), or Credentials if the target already exists"]);
  if (maintenance) rows.push(["maintenance", adminUser, "maintenance", "Credentials > + Add Credential Purpose" + (effConsole === "maintenance" ? " (also covers the console: VM.Console is in this role)" : "")]);
  if (effConsole === "separate") rows.push(["console", consoleUser, "console", "Credentials > + Add Credential Purpose"]);

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">
        Run <b>once per cluster</b>. It creates the service accounts, roles and API tokens <i>inside Proxmox</i>. You copy the script to any Proxmox node and run it yourself; PyXie never creates Proxmox accounts. Nothing is ticked yet: choose what you want PyXie to have. {SETUP_LINK}
      </p>

      <div className="space-y-3">
        <div className="space-y-1">
          <Check checked={inventory} onChange={(v) => { setInventory(v); touch(); }} label="Inventory token (read-only)" sub="Built-in PVEAuditor role. Discovery and monitoring. Needed for PyXie to work at all." />
          {inventory && <UserField label="Read-only account name" value={roUser} def="pyxie-ro@pve" onChange={(v) => { setRoUser(v); touch(); }} ok={okName(roUser)} />}
        </div>
        <div className="space-y-1">
          <Check checked={maintenance} onChange={(v) => { setMaintenance(v); touch(); }} label="Admin (Maintenance) token (write)" sub="Role PyXieAdmin: migrations, power, reboots. Still gated by PyXie's own Settings switch and your approval of each action." />
          {maintenance && <UserField label="Admin account name" value={adminUser} def="pyxie-admin@pve" onChange={(v) => { setAdminUser(v); touch(); }} ok={okName(adminUser)} />}
        </div>
        <div className="space-y-1">
          <div className="text-sm text-text">Embedded VM console</div>
          <div className="pl-6 space-y-1">
            {([
              ["none", "Not needed"],
              ["maintenance", "Add VM.Console to the admin role (needs the admin token above; no extra account)"],
              ["separate", "Separate console token (VM.Console only, smaller blast radius)"],
            ] as [ConsoleOpt, string][]).map(([v, label]) => (
              <label key={v} className={`flex items-center gap-2 text-sm ${v === "maintenance" && !maintenance ? "opacity-50" : ""}`}>
                <input type="radio" name="console-opt" checked={effConsole === v} disabled={v === "maintenance" && !maintenance}
                  onChange={() => { setConsoleOpt(v); touch(); }} />
                {label}
              </label>
            ))}
          </div>
          {effConsole === "separate" && <UserField label="Console account name" value={consoleUser} def="pyxie-console@pve" onChange={(v) => { setConsoleUser(v); touch(); }} ok={okName(consoleUser)} />}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button type="button" disabled={!pve} onClick={() => setGenerated(true)}
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50">
          Generate the account script
        </button>
        {!anyPve && <span className="text-xs text-muted">Tick at least one token first.</span>}
      </div>

      {generated && pve && (
        <div className="space-y-3">
          <ScriptBox title="Proxmox accounts script (run once, as root, on any node)"
            hint={`Paste it into a root shell on any node (safe to paste: an error prints "STOPPED at line ..." and cannot close your session; safe to re-run). Proxmox shows each token secret only once, so the output is also saved to ${TOKEN_FILE}.`}
            text={pve} filename="pyxie-pve-setup.sh" />
          <div className="border border-border rounded p-3 space-y-2">
            <div className="text-sm font-medium text-text">After it runs: put each token into PyXie</div>
            <p className="text-xs text-muted">
              A new Proxmox account does <b>not</b> appear in PyXie by itself. For each token below, copy its <b>secret</b> from <code>{TOKEN_FILE}</code> and enter it in PyXie as shown, using the token user and token id exactly as listed (the script names the token after its purpose).
            </p>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-muted"><th className="py-1 pr-3">Purpose</th><th className="pr-3">Token user</th><th className="pr-3">Token ID</th><th>Where in PyXie</th></tr></thead>
              <tbody>
                {rows.map(([purpose, user, id, where]) => (
                  <tr key={purpose} className="border-t border-border align-top">
                    <td className="py-1.5 pr-3 font-mono text-text">{purpose}</td>
                    <td className="pr-3 font-mono">{user}</td>
                    <td className="pr-3 font-mono">{id}</td>
                    <td className="text-muted">{where}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <ol className="list-decimal pl-5 text-xs text-muted space-y-0.5">
              <li>In <a className="text-accent hover:underline" href="/platform/credentials">Credentials</a>, choose <b>+ Add Credential Purpose</b>, pick the purpose from the list, and paste the secret. Then press <b>Test Connection</b> on that row: if it fails, the reason is shown.</li>
              <li>When every secret is in PyXie, delete the file on the node: <code>shred -u {TOKEN_FILE}</code></li>
              {effConsole === "maintenance" && <li>The console uses the admin (maintenance) credential, so there is no separate console credential to add. Turn the console on in Settings afterwards.</li>}
              {effConsole === "separate" && <li>Turn the console on in Settings after the console credential tests as valid.</li>}
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}

/** The second card: the wrapper on each node. YOU run the script on the node; PyXie never installs it itself. */
export function HostWrapperBuilder({ targets, tlsMode }: { targets: Target[]; tlsMode: string }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [targetId, setTargetId] = useState(targets[0]?.id ?? "");
  const noTarget = targets.length === 0;
  const selfSigned = tlsMode === "internal";
  const [hostMode, setHostMode] = useState<"" | "install" | "uninstall">("");
  const [insecure, setInsecure] = useState(selfSigned);
  const [link, setLink] = useState<Link | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [needKey, setNeedKey] = useState(false);
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const touch = () => setGenerated(false);

  async function generate() {
    setError(null);
    setBusy(true);
    try {
      const r = await fetch(`/api/pve-targets/${targetId}/host-kit/link`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        if (r.status === 409 && /key pair|host-maintenance credential/i.test(d.error || "")) setNeedKey(true);
        throw new Error(r.status === 409 ? "The host key pair has not been generated yet." : d.error || `Request failed (${r.status})`);
      }
      setNeedKey(false);
      setLink(d);
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
      <div className="text-xs text-muted space-y-1.5">
        <p>
          <b>You run this script yourself, as root, on each Proxmox node.</b> PyXie does not connect to the node to install it. Run it on <b>every</b> node you want PyXie to patch and reboot; running it again later upgrades the wrapper. {SETUP_LINK}
        </p>
        <p>
          <b>What it installs on the node:</b> a locked-down SSH identity <code>pyxie-hostmaint</code> (key only, no shell, seven fixed commands), the wrapper <code>/usr/local/sbin/pyxie-maint</code>, and a sudoers entry <code>/etc/sudoers.d/pyxie-maint</code>. It lets PyXie list and apply package updates, show live update output, and reboot the node. It does nothing else.
        </p>
        <p className="text-warn">
          Impact to know about: once the wrapper is installed, applying updates from PyXie can <b>reboot that node</b> (kernel updates need it). Plan it through maintenance mode so its VMs are moved off first. Installing the wrapper itself does not reboot anything.
        </p>
      </div>

      {noTarget && <p className="text-xs text-warn">The host script needs a connected PVE target (step 3), so it is not available yet.</p>}
      {targets.length > 1 && (
        <label className="block text-xs text-muted space-y-1">
          <span className="text-text">PVE target</span>
          <select className={INPUT} value={targetId} onChange={(e) => { setTargetId(e.target.value); touch(); }}>
            {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
      )}

      <div className="space-y-2">
        <div className="text-xs uppercase tracking-wide text-muted">What do you want to do on the node?</div>
        {([
          ["install", "Install or upgrade the host wrapper", "Safe to run again: an up-to-date node just reports that nothing changed."],
          ["uninstall", "Remove the host wrapper", "Removes the PyXie SSH identity, wrapper and sudoers entry from the node."],
        ] as ["install" | "uninstall", string, string][]).map(([v, label, sub]) => (
          <label key={v} className="flex items-start gap-2 text-sm">
            <input type="radio" name="host-opt" className="mt-1" checked={hostMode === v} disabled={noTarget} onChange={() => { setHostMode(v); touch(); }} />
            <span><span className="text-text">{label}</span><span className="block text-xs text-muted">{sub}</span></span>
          </label>
        ))}
      </div>

      {hostMode !== "" && (
        <div className="space-y-1">
          <Check checked={insecure} onChange={(v) => { setInsecure(v); touch(); }} label="This node does not trust PyXie's certificate"
            sub="Adds curl -k to the download. The script still verifies the downloaded file against the checksum PyXie gives it, so a tampered file is refused." />
          <p className="text-xs text-muted pl-6">
            {selfSigned
              ? "This PyXie uses its own internal certificate authority (self-signed), which your nodes do not know, so this is ticked for you. Without it the download fails with “curl: (60) SSL certificate problem”."
              : tlsMode
                ? "This PyXie serves a certificate your nodes will usually trust, so it is not ticked. If the download fails with “curl: (60) SSL certificate problem”, tick it and generate again."
                : "If the download fails with “curl: (60) SSL certificate problem”, the node does not trust this PyXie's certificate: tick this and generate again."}
          </p>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button type="button" disabled={busy || noTarget || hostMode === ""} onClick={generate}
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50">
          {busy ? "Generating…" : "Generate the host script"}
        </button>
        {hostMode === "" && !noTarget && <span className="text-xs text-muted">Choose what to do first.</span>}
        {error && <span className="text-sm text-bad">{error}</span>}
        {needKey && (
          <button type="button" onClick={generateKeyThenRetry} disabled={busy}
            className="px-3 py-1.5 rounded text-sm border border-warn hover:bg-warn/10 disabled:opacity-50">Generate the host key pair now</button>
        )}
      </div>

      {generated && link && hostMode !== "" && (
        <div className="space-y-3">
          <ScriptBox
            title={`Host wrapper script (${hostMode === "install" ? "install or upgrade" : "remove"}; run as root on the node)`}
            hint={`The download link expires in ${Math.round(link.expires_in / 60)} minutes (generate again after that). Expected checksum ${link.sha256.slice(0, 16)}…`}
            text={hostScript({ origin, link, mode: hostMode, insecure })} filename="pyxie-host-setup.sh" />
          <AfterGenerate targetId={targetId} sha256={link.sha256} mode={hostMode} />
          <p className="text-xs text-muted">
            Nothing on this page runs anything on your nodes; you review and run the scripts yourself. After the wrapper is installed, pin the node&apos;s SSH host key on the Credentials page (shown above). On an <i>upgrade</i> of a node that was already set up, the script says so and there is nothing more to do.
          </p>
        </div>
      )}
    </div>
  );
}
