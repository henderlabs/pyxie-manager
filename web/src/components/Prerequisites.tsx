/** What a new installation needs before anything else, with suggested sizing. Plain content, shared by the Quick start
 * page and the Integrations setup guide (collapsed there). The numbers come from the lab (4 nodes, 24 guests) and the
 * README's two production deployments; they are starting points, not a formula. */

const SIZES: [string, string, string, string, string][] = [
  ["Small", "up to 4 nodes, 50 guests", "2", "4 GB", "40 GB"],
  ["Medium", "up to 16 nodes, 300 guests", "4", "8 GB", "80 GB"],
  ["Large", "more than that", "8", "16 GB", "120 GB+"],
];

export default function Prerequisites({ collapsible = false }: { collapsible?: boolean }) {
  const body = (
    <div className="space-y-4 text-sm text-text">
      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-1">1. A VM for PyXie</div>
        <ul className="list-disc pl-5 space-y-1 text-xs">
          <li>One Linux VM, Ubuntu 24.04 LTS (tested). It runs everything as Docker containers: the web app, the API, the background worker, PostgreSQL, Redis, and <span className="font-medium">Caddy</span>, the HTTPS front door. You do not install Caddy, PostgreSQL or Redis yourself.</li>
          <li><span className="font-medium">Docker Engine with the Compose plugin</span>, installed by a person as root: <code>sudo bash ops/docker/install-docker.sh</code>. The login user must be in the <code>docker</code> group.</li>
          <li><code>git</code>, <code>python3</code>, <code>cron</code>, <code>curl</code>: standard on Ubuntu. The installer, the in-app updater and the daily backup use them.</li>
          <li>It can run as a VM on the cluster it manages. PyXie refuses to power off or force-stop its own VM.</li>
        </ul>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-1">2. Suggested size (starting points)</div>
        <table className="w-full text-xs">
          <thead><tr className="text-left text-muted"><th className="py-1 pr-3">Size</th><th className="pr-3">Fits</th><th className="pr-3">vCPU</th><th className="pr-3">RAM</th><th>Disk</th></tr></thead>
          <tbody>
            {SIZES.map(([n, fits, c, r, d]) => (
              <tr key={n} className="border-t border-border"><td className="py-1 pr-3 font-medium">{n}</td><td className="pr-3 text-muted">{fits}</td><td className="pr-3">{c}</td><td className="pr-3">{r}</td><td>{d}</td></tr>
            ))}
          </tbody>
        </table>
        <ul className="list-disc pl-5 space-y-1 text-xs text-muted mt-2">
          <li>Measured on two real installs. A lab with 4 nodes and 24 guests: the whole stack used about 0.5 GB of RAM, almost no CPU, and a 0.6 GB database. A production cluster with 8 nodes and 130 guests: about 0.9 GB of RAM, a 2.9 GB database (almost all of it 10 months of metric history), 2 GB of backups, and 22 GB of disk in use. The database can briefly use two CPU cores while it collects. Both fit the sizes above with room to spare.</li>
          <li>RAM and CPU spike for about a minute during an update, while the new version is built next to the running one. Give it at least 2 vCPU and 4 GB so that build never starves the app.</li>
          <li>Disk: about 30 GB, plus roughly 0.1 GB per node or guest PyXie watches. That covers 400 days of metric history (30 to 60 MB per node or guest) and the backups. Docker keeps build cache for every image it builds (up to 15 GB seen on a server where builds were run by hand over and over); the updater now caps it at 3 GB after each update, and <code>docker builder prune</code> clears it any time, which is why the base is generous. The Run check step reports this server&apos;s CPU, RAM and disk against these numbers.</li>
        </ul>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-1">3. Network</div>
        <ul className="list-disc pl-5 space-y-1 text-xs">
          <li>A DNS name for the VM (for example <code>pyxie.example.com</code>) that resolves to it. People reach it on port 443; port 80 redirects.</li>
          <li>From the VM to every Proxmox node: TCP 8006 (the API), and TCP 22 (SSH) if you will patch hosts through PyXie.</li>
          <li>From the VM out to GitHub over HTTPS (release checks and updates), and to your mail relay if you want email alerts.</li>
          <li>From a Proxmox node to the VM on 443, once, to download the host installer (or copy the file over yourself).</li>
          <li>Accurate time (NTP) on the VM.</li>
        </ul>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-1">4. The HTTPS certificate (Caddy)</div>
        <p className="text-xs text-muted mb-1">Choose one with <code>PYXIE_TLS_MODE</code> in <code>.env</code>, then <code>docker compose up -d pyxie-manager-caddy</code>.</p>
        <ul className="list-disc pl-5 space-y-1 text-xs">
          <li><span className="font-medium">internal</span> (default): Caddy signs its own certificate. Quickest for a lab. Browsers warn until you trust its root certificate, and a Proxmox node will not trust it either, so the host script needs the &quot;does not trust PyXie&apos;s certificate&quot; option (still safe: the download is checked against a checksum).</li>
          <li><span className="font-medium">file</span>: put your own certificate and key in <code>./certs/pyxie.crt</code> and <code>pyxie.key</code> (full chain). Use this for a certificate your organisation already issues. Caddy does not renew it: replace the files and reload Caddy before it expires.</li>
          <li><span className="font-medium">acme</span>: for a private certificate authority that speaks ACME. It needs <code>ops/caddy/tls/acme.caddy</code> filled in and <code>ACME_CA</code> set; Caddy then renews by itself. Not set up by default.</li>
        </ul>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-1">5. Access you will need</div>
        <ul className="list-disc pl-5 space-y-1 text-xs">
          <li>Read access to the PyXie repository on GitHub (a fine-grained token, stored in a git credential helper on the VM).</li>
          <li>Someone who can run commands as root on a Proxmox node, to create the two accounts and install the host wrapper. PyXie never does that for you.</li>
        </ul>
      </div>
    </div>
  );
  if (!collapsible) return body;
  return (
    <details className="border border-border rounded-lg p-3 mb-3">
      <summary className="text-sm font-medium text-text cursor-pointer">Before you start: what you need (VM, Docker, Caddy, network, size)</summary>
      <div className="mt-3">{body}</div>
    </details>
  );
}
