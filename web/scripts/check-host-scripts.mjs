// Writes the generated setup scripts to disk so ops/tests/check-host-scripts.sh can run them for real.
// Run with Node 22+: node --experimental-strip-types web/scripts/check-host-scripts.mjs <outdir>
import { writeFileSync, mkdirSync } from "node:fs";
import { pveScript, hostScript } from "../src/lib/hostScripts.ts";

const out = process.argv[2] || "/tmp/pyxie-scripts";
mkdirSync(out, { recursive: true });
writeFileSync(`${out}/pve-both.sh`, pveScript({ roUser: "pyxie-ro@pve", adminUser: "pyxie-admin@pve", consoleUser: "pyxie-console@pve", inventory: true, maintenance: true, console: "maintenance" }));
writeFileSync(`${out}/pve-separate-console.sh`, pveScript({ roUser: "pyxie-ro@pve", adminUser: "pyxie-admin@pve", consoleUser: "pyxie-console@pve", inventory: true, maintenance: true, console: "separate" }));
writeFileSync(`${out}/host-install.sh`, hostScript({ origin: "https://pyxie.example", link: { path: "/host-kit/TOKEN", sha256: "a".repeat(64), expires_in: 1800, kit_version: "0.27.8", wrapper_version: "1.1.0" }, mode: "install", insecure: true }));
console.log("wrote", out);
