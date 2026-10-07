// Generators for the setup-builder scripts. Plain TypeScript with no React, so web/scripts/check-host-scripts.mjs can run
// them for real. Both scripts are PASTE-SAFE: the work runs inside its own `bash -s`, so `set -e` can never close the
// shell the script was pasted into, and an error names the failing line instead of just ending the SSH session.

export type ConsoleOpt = "none" | "maintenance" | "separate";
export type Link = { path: string; sha256: string; expires_in: number; kit_version: string; wrapper_version: string };

export const MAINT_PRIVS = "Datastore.Allocate,Datastore.AllocateSpace,Sys.Audit,Sys.Modify,Sys.PowerMgmt,VM.Audit,VM.Config.CPU,VM.Config.Disk,VM.Config.Memory,VM.Migrate,VM.PowerMgmt";

export const TOKEN_FILE = "/root/pyxie-tokens.txt";

export function pveScript(o: { roUser: string; adminUser: string; consoleUser: string; inventory: boolean; maintenance: boolean; console: ConsoleOpt }): string {
  const B: string[] = [
    "set -Eeuo pipefail",
    `trap 'echo "STOPPED at line $LINENO: $BASH_COMMAND" >&2' ERR`,
    'command -v pveum >/dev/null || { echo "pveum not found: run this on a Proxmox VE node" >&2; exit 1; }',
    'ensure_user() { pveum user add "$1" --comment "$2" 2>/dev/null || echo "user $1 already exists"; }',
    'have_token() { local out; out=$(pveum user token list "$1" --output-format json 2>/dev/null || true); grep -Eq "\\"tokenid\\": *\\"$2\\"" <<<"$out"; }',
    'add_token() { if have_token "$1" "$2"; then echo "token $1!$2 already exists"; else pveum user token add "$1" "$2" --privsep 1 --comment "$3"; fi; }',
    'grant() { pveum acl modify "$1" --roles "$2" --users "$3"; pveum acl modify "$1" --roles "$2" --tokens "$3!$4"; }',
  ];
  const users: string[] = [];
  if (o.inventory) {
    users.push(o.roUser);
    B.push("", "# Read-only account: inventory token (built-in PVEAuditor role)", `ensure_user '${o.roUser}' "PyXie read-only (inventory)"`, `add_token '${o.roUser}' inventory "PyXie inventory (read-only)"`, `grant / PVEAuditor '${o.roUser}' inventory`);
  }
  if (o.maintenance) {
    users.push(o.adminUser);
    const privs = MAINT_PRIVS + (o.console === "maintenance" ? ",VM.Console" : "");
    B.push(
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
    B.push("", "# Console account: its own token, VM.Console only (built-in role PVEVMConsole)", `ensure_user '${o.consoleUser}' "PyXie embedded console"`, `add_token '${o.consoleUser}' console "PyXie embedded console"`, `grant /vms PVEVMConsole '${o.consoleUser}' console`);
  }
  B.push("", 'echo ""', 'echo "DONE. Tokens on these accounts (secrets are not shown again):"');
  for (const u of users) B.push(`pveum user token list '${u}'`);
  return [
    "#!/bin/bash",
    "# PyXie Manager: Proxmox VE service accounts, roles and API tokens.",
    "# Run ONCE, as root, on any node of the cluster. Safe to re-run: existing users, roles and tokens are left alone.",
    "# Safe to paste straight into a root shell: the work runs inside its own bash, so an error cannot close your session",
    "# and a failure prints 'STOPPED at line ...'.",
    `# Proxmox shows each token secret exactly once, so everything printed is also saved (root-only) to ${TOKEN_FILE}.`,
    `(umask 077; bash -s 2>&1 <<'PYXIE_SETUP' | tee ${TOKEN_FILE}`,
    ...B,
    "PYXIE_SETUP",
    'exit "${PIPESTATUS[0]}"',
    ")",
    "if [ $? -eq 0 ]; then",
    '  echo ""',
    `  echo "Token secrets are saved in ${TOKEN_FILE}. Copy them into PyXie, then delete the file:"`,
    `  echo "    cat ${TOKEN_FILE}   then   shred -u ${TOKEN_FILE}"`,
    "else",
    '  echo ""',
    `  echo "The script stopped (see the STOPPED line above). Nothing was removed; it is safe to run it again. Any secrets already printed are in ${TOKEN_FILE}."`,
    "fi",
    "",
  ].join("\n");
}

export function hostScript(o: { origin: string; link: Link; mode: "install" | "uninstall"; insecure: boolean }): string {
  const url = `${o.origin}${o.link.path}`;
  return [
    "#!/bin/bash",
    `# PyXie Manager: ${o.mode === "install" ? "install or upgrade" : "remove"} the host-maintenance wrapper on THIS node (kit ${o.link.kit_version}, wrapper ${o.link.wrapper_version}).`,
    "# Run as root on EVERY Proxmox node you want PyXie to patch and reboot. Safe to re-run.",
    `# The download link expires in ${Math.round(o.link.expires_in / 60)} minutes. The checksum below comes from PyXie: the script refuses to run if the file differs.`,
    "# Safe to paste straight into a root shell: it runs inside its own bash, so an error cannot close your session.",
    "bash -s <<'PYXIE_HOST'",
    "set -Eeuo pipefail",
    `trap 'echo "STOPPED at line $LINENO: $BASH_COMMAND" >&2' ERR`,
    '[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }',
    'F="$(mktemp /tmp/pyxie-host-kit.XXXXXX)"',
    "trap 'rm -f \"$F\"' EXIT",
    `curl -fsS${o.insecure ? "k" : ""} -o "$F" '${url}'`,
    `echo "${o.link.sha256}  $F" | sha256sum -c -`,
    `bash "$F"${o.mode === "uninstall" ? " --uninstall" : ""}`,
    "PYXIE_HOST",
    "",
  ].join("\n");
}
