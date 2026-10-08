// Capture real PyXie pages from the DEMO stack using headless Chrome + CDP (Node 22+, Chrome installed).
// Usage: PORT=13000 DEMO_PASSWORD=... W=1600 H=1000 DSF=1.25 node shoot.mjs <outdir> "$(cat pages.json)"
// A page may carry `run` (JS to run before the capture, e.g. click a button) and `clip` (JS returning a rect).
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";

const BASE = `http://127.0.0.1:${process.env.PORT || 13000}`;
const OUT = process.argv[2];
const PAGES = JSON.parse(process.argv[3]); // [{name, path, clipSelector?, wait?}]
const W = Number(process.env.W || 1440), H = Number(process.env.H || 900), DSF = Number(process.env.DSF || 2);
mkdirSync(OUT, { recursive: true });

const login = await fetch(`${BASE}/api/auth/login`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ email: "admin@example.com", password: process.env.DEMO_PASSWORD }),
});
if (!login.ok) throw new Error("login failed " + login.status);
const cookies = login.headers.getSetCookie().map((c) => {
  const [nv] = c.split(";");
  const i = nv.indexOf("=");
  return { name: nv.slice(0, i), value: nv.slice(i + 1) };
});

rmSync("/tmp/pxshots-profile", { recursive: true, force: true });
const chrome = spawn(process.env.CHROME || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "google-chrome"), [
  "--headless=new", "--remote-debugging-port=9333", "--user-data-dir=/tmp/pxshots-profile", ...(process.platform === "linux" ? ["--no-sandbox"] : []),
  "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "--disable-gpu", "about:blank",
], { stdio: "ignore" });

async function targets() {
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch("http://127.0.0.1:9333/json");
      const t = (await r.json()).find((x) => x.type === "page");
      if (t) return t;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("chrome did not start");
}
const t = await targets();
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map(); const events = [];
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else events.push(d);
};
const send = (method, params = {}) => new Promise((res, rej) => {
  const i = ++id; pending.set(i, (d) => (d.error ? rej(new Error(JSON.stringify(d.error))) : res(d.result)));
  ws.send(JSON.stringify({ id: i, method, params }));
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Network.enable");
for (const c of cookies) await send("Network.setCookie", { ...c, domain: "127.0.0.1", path: "/", httpOnly: true });
await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: DSF, mobile: false });
await send("Page.enable");

for (const p of PAGES) {
  await send("Page.navigate", { url: BASE + p.path });
  await sleep(p.wait ?? 4500);
  if (p.run) { await send("Runtime.evaluate", { expression: p.run, awaitPromise: true }); await sleep(p.runWait ?? 4000); }
  let clip;
  if (p.clip) {
    const r = await send("Runtime.evaluate", { expression: p.clip, returnByValue: true });
    clip = r.result.value && { ...r.result.value, scale: 1 };
  }
  const shot = await send("Page.captureScreenshot", { format: "png", ...(clip ? { clip } : {}) });
  writeFileSync(`${OUT}/${p.name}.png`, Buffer.from(shot.data, "base64"));
  console.log("saved", p.name, clip ? JSON.stringify(clip) : "(viewport)");
}
ws.close(); chrome.kill();
