import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import { LightbulbIcon } from "@/components/Icons";
import type { SetupStatus } from "@/components/SetupGuide";

const PRIVS: [string, string][] = [
  ["Datastore.Allocate", "Create and remove storage-backed resources when a guest or disk moves."],
  ["Datastore.AllocateSpace", "Allocate space on a storage for migrations, disk moves and resizes."],
  ["Sys.Audit", "Read a node's status."],
  ["Sys.Modify", "List pending package updates and refresh a node's package lists (Proxmox requires this, not just Sys.Audit)."],
  ["Sys.PowerMgmt", "Reboot a host."],
  ["VM.Audit", "Read a guest's state."],
  ["VM.Config.CPU", "Resize CPU."],
  ["VM.Config.Memory", "Resize memory and ballooning."],
  ["VM.Config.Disk", "Resize disks."],
  ["VM.Migrate", "Live and offline migration, evacuation."],
  ["VM.PowerMgmt", "Start, stop, shut down and reboot guests."],
  ["VM.Console (optional)", "The embedded console. Added only if you choose it in the script builder."],
];

function chip(done: boolean | undefined, doneText: string, todoText: string) {
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded ${done ? "bg-good/15 text-good" : "bg-surface2 text-muted"}`}>
      {done ? doneText : todoText}
    </span>
  );
}

export default async function QuickStartPage() {
  const status = await apiFetch<SetupStatus>("/api/setup/status").catch(() => null);
  const step = (k: string) => status?.steps.find((s) => s.key === k);
  const progress = status?.progress;

  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Quick start"
        subtitle="New installation? About 15 minutes: create two Proxmox accounts, give PyXie one token from each, and test them. Nothing in Proxmox changes until you switch writes on."
        icon={<LightbulbIcon className="w-5 h-5" />}
      />

      <Card className="mb-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="text-sm text-text">
            {progress ? `${progress.done} of ${progress.total} setup steps done.` : "Setup status is not available right now."}
          </div>
          <Link href="/platform/providers" className="ml-auto px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10">
            Open the setup guide
          </Link>
        </div>
      </Card>

      <div className="grid md:grid-cols-2 gap-4 mb-4">
        <Card>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] px-2 py-0.5 rounded bg-good/15 text-good">Token 1 · Read-only</span>
            {chip(step("cluster")?.state === "done", "Connected", "Not connected yet")}
          </div>
          <h2 className="text-base font-semibold text-text">Inventory</h2>
          <p className="text-xs text-muted mb-3">Lets PyXie look. It cannot change anything.</p>
          <Facts title="In Proxmox" items={["User pyxie-ro@pve, token ID inventory", "Role PVEAuditor (built in) at /, for the user and for the token", "Privilege separation on"]} />
          <Facts title="PyXie uses it for" items={[
            "Discovery and sync of hosts, VMs and storage",
            "Every preview (dry run) of every action: migration, evacuation, maintenance, reboots, resize, balance",
            "Health findings, right-sizing, capacity, reports and metrics history",
            "Live VM and memory data, and the \"is this VM really responding\" check",
          ]} />
          <Facts title="It cannot" items={["Start, stop, migrate or resize anything", "List pending package updates (Proxmox wants Sys.Modify for that)"]} />
          <Facts title="In PyXie" items={["Setup guide step 3: Connect your cluster (Integrations page)", "Token user pyxie-ro@pve, token ID inventory, the secret, then Test connection"]} />
        </Card>

        <Card>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] px-2 py-0.5 rounded bg-warn/15 text-warn">Token 2 · Admin</span>
            {chip(step("admin")?.state === "done", "Tested", step("admin")?.state === "waiting" ? "Waiting on token 1" : "Not set up yet")}
          </div>
          <h2 className="text-base font-semibold text-text">Maintenance (Admin)</h2>
          <p className="text-xs text-muted mb-3">Lets PyXie act, after you preview and approve each action.</p>
          <Facts title="In Proxmox" items={["User pyxie-admin@pve, token ID maintenance", "Role PyXieAdmin (custom, privileges below) at /, for the user and for the token", "Privilege separation on"]} />
          <Facts title="PyXie uses it for" items={[
            "Migration, evacuation and maintenance mode",
            "Start, stop, reboot and resize guests",
            "Host reboots, and pending-update counts and task logs",
            "Optional: the embedded console (adds VM.Console)",
          ]} />
          <Facts title="Safety" items={[
            "Off until Settings > Allow PyXie to write to Proxmox VE is switched on",
            "Every action is previewed, safety-checked and approved by you first",
            "Scoped at the root (/), a documented, accepted trade-off: see the README's Safety Contract",
          ]} />
          <Facts title="In PyXie" items={["Setup guide step 4 (Credentials page): add purpose maintenance (admin)", "Token user pyxie-admin@pve, token ID maintenance, the secret, then Test connection"]} />
        </Card>
      </div>

      <Card className="mb-4">
        <CardTitle>The 6 steps</CardTitle>
        <ol className="space-y-2 text-sm text-text">
          <Row n={1} title="Add a site" body="A location or grouping, such as Lab. Setup guide step 1." />
          <Row n={2} title="Create both accounts in Proxmox" body="Easiest: open the script builder on the Integrations page, tick the inventory and maintenance tokens, copy the script to any Proxmox node and run it as root. It is safe to re-run. Prefer clicking? See Doing it by hand, below." />
          <Row n={3} title="Save the two token secrets" body="Proxmox shows each secret exactly once, when the token is created. Copy them somewhere safe right away; if you lose one, regenerate it in Proxmox." />
          <Row n={4} title="Connect the cluster with token 1" body="Integrations > Add PVE target: the hostname or IP of any one node, Verify TLS off for a stock self-signed certificate, token user, token ID and secret. Click Test connection, then Sync now." />
          <Row n={5} title="Add token 2" body="Credentials > Add credential purpose > maintenance (admin). Click Test connection on that row. The target-level test only checks token 1." />
          <Row n={6} title="Connect each host and switch features on" body="To apply updates and reboot hosts: generate the host key pair, run the host script from the builder on every node, pin each node's SSH host key. Then, when you are ready, Settings > Allow PyXie to write to Proxmox VE." />
        </ol>
        <div className="flex gap-2 mt-3 flex-wrap">
          <Link href="/platform/providers#builder" className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface2">Open the script builder</Link>
          <Link href="/platform/credentials" className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface2">Credentials</Link>
          <Link href="/platform/settings" className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface2">Settings</Link>
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>What the PyXieAdmin role allows, and why</CardTitle>
        <p className="text-xs text-muted mb-2">Deliberately narrower than Proxmox&apos;s built-in PVEAdmin: only what PyXie&apos;s write paths call.</p>
        <table className="w-full text-sm">
          <tbody>
            {PRIVS.map(([p, why]) => (
              <tr key={p} className="border-t border-border align-top">
                <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap text-text">{p}</td>
                <td className="py-1.5 text-muted text-xs">{why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card className="mb-4">
        <CardTitle>Doing it by hand in the Proxmox web interface</CardTitle>
        <details>
          <summary className="text-sm cursor-pointer text-accent">Show the clicks</summary>
          <ol className="list-decimal pl-5 text-sm text-text space-y-1.5 mt-2">
            <li>Datacenter &gt; Permissions &gt; Users &gt; Add: <code>pyxie-ro</code> and <code>pyxie-admin</code>, realm Proxmox VE authentication server (pve).</li>
            <li>Datacenter &gt; Permissions &gt; Roles &gt; Add: name <code>PyXieAdmin</code>, tick the privileges in the table above.</li>
            <li>Datacenter &gt; Permissions &gt; API Tokens &gt; Add: user <code>pyxie-ro@pve</code>, token ID <code>inventory</code>; user <code>pyxie-admin@pve</code>, token ID <code>maintenance</code>. Keep Privilege Separation ticked, Expire never. Copy each secret now.</li>
            <li>Datacenter &gt; Permissions &gt; Add &gt; User Permission, path <code>/</code>: <code>pyxie-ro@pve</code> with PVEAuditor; <code>pyxie-admin@pve</code> with PyXieAdmin.</li>
            <li>Datacenter &gt; Permissions &gt; Add &gt; API Token Permission, path <code>/</code>: <code>pyxie-ro@pve!inventory</code> with PVEAuditor; <code>pyxie-admin@pve!maintenance</code> with PyXieAdmin. (With privilege separation a token needs its own grant as well as its user.)</li>
          </ol>
        </details>
      </Card>

      <Card className="mb-8">
        <CardTitle>Good to know</CardTitle>
        <ul className="list-disc pl-5 text-sm text-text space-y-1.5">
          <li>PyXie never sees a Proxmox password. It only holds the two token secrets, stored encrypted.</li>
          <li>Two independent gates guard every change: the admin credential and the Settings write switch. Both must allow it, plus your approval on the action.</li>
          <li>Rotate a secret: regenerate it on the token in Proxmox, then paste the new secret in Credentials &gt; Edit.</li>
          <li>Accounts created earlier with other names (for example pyxie-manager@pve and a role called PyXieMaintenanceW1) keep working: those are only labels in Proxmox.</li>
          <li>Run the checks at any time: Integrations &gt; step 8, Check everything.</li>
        </ul>
      </Card>
    </div>
  );
}

function Facts({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="mb-2">
      <div className="text-[11px] uppercase tracking-wide text-muted mb-0.5">{title}</div>
      <ul className="list-disc pl-5 text-xs text-text space-y-0.5">{items.map((i) => <li key={i}>{i}</li>)}</ul>
    </div>
  );
}

function Row({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <li className="flex gap-3">
      <span className="w-6 h-6 rounded-full bg-accent/15 text-accent text-xs flex items-center justify-center flex-none mt-0.5">{n}</span>
      <span><span className="font-medium">{title}.</span> <span className="text-muted text-xs">{body}</span></span>
    </li>
  );
}
