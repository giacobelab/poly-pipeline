// Page test for a multi-VM lab page (web/multi.html), in headless Chrome.
//
//   node page-test-multi.mjs <url> <screenshot-dir> [email]
//
// 1. enter an email and start; the visible VM boots and is logged in as root
// 2. every hidden VM (each in its own Web Worker) reaches its login prompt
// 3. the hub carries frames from every VM
// 4. on the visible VM: eth0 has an address, it has seen at least two other
//    hosts on the segment (ip neigh), and the identity file the lab writes
//    holds the email that was entered
import puppeteer from "puppeteer-core";
import { mkdirSync } from "node:fs";

const [url, shots = "page-shots", email = "ci-test@psu.edu"] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const BOOT_S = Number(process.env.PAGE_BOOT_TIMEOUT || 420);
const say = (...a) => console.log(...a);
let page;

async function fail(msg) {
  try {
    await page.screenshot({ path: `${shots}/failure.png` });
    const info = await page.evaluate(() => ({ state: window.polylab?.state, frames: window.polylab?.frames,
      errors: window.polylab?.errors, serial: Object.fromEntries(Object.entries(window.polylab?.serial || {})
        .map(([k, v]) => [k, v.slice(-15)])) }));
    say("::group::Page state at failure");
    say(JSON.stringify(info, null, 2));
    say("::endgroup::");
  } catch (e) {}
  say(`::error::page test failed: ${msg}`);
  process.exit(1);
}

const chrome = process.env.CHROME_PATH || "/usr/bin/google-chrome";
const browser = await puppeteer.launch({ executablePath: chrome, headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"] });
page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900 });
page.on("pageerror", e => say("page error:", e.message));
page.on("console", m => { if (m.type() === "error") say("console error:", m.text()); });

await page.goto(url, { waitUntil: "load" });
await page.type("#email", email);
await page.click("#go");
say(`started with ${email}`);

const screen = () => page.evaluate(() => document.querySelector("#screen_container > div").innerText);
async function waitFor(what, test, seconds, arg) {
  const t0 = Date.now();
  while ((Date.now() - t0) / 1000 < seconds) {
    if (await page.evaluate(test, arg)) { say(`${what} after ${Math.round((Date.now() - t0) / 1000)}s`); return; }
    const errors = await page.evaluate(() => window.polylab.errors);
    if (errors.length) await fail(`VM error: ${errors.join("; ")}`);
    await new Promise(r => setTimeout(r, 1000));
  }
  await fail(`timed out after ${seconds}s waiting for: ${what}`);
}

const vms = await page.evaluate(() => fetch("images/vms.json").then(r => r.json()));
const visible = vms.find(v => v.visible).name;
const hidden = vms.filter(v => !v.visible).map(v => v.name);
say(`VMs: ${visible} (visible), ${hidden.join(", ")} (hidden, in workers)`);

await waitFor(`${visible} logged in`, () => document.getElementById("status-text").textContent.startsWith("Ready"), BOOT_S);
for (const name of hidden) {
  await waitFor(`${name} at its login prompt`, n => window.polylab.state[n] === "running", BOOT_S, name);
}
// Wait until every VM has sent some traffic (the lab's programs start after
// the login prompt, and Python starts slowly in v86), then let it flow a bit.
await waitFor("network traffic from every VM", names =>
  names.every(n => (window.polylab.frames[n] || 0) >= 5), Number(process.env.PAGE_TRAFFIC_TIMEOUT || 240),
  [visible, ...hidden]);
await new Promise(r => setTimeout(r, 10000));

await page.evaluate(() => window.emulator.keyboard_send_text(
  "clear; echo IP:; ip -4 -o addr show eth0; echo NEIGH:; ip neigh show dev eth0; " +
  "echo ID:; head -c 300 /var/lib/snort-lab/identity; echo; echo END-OF-CHECK\n"));
await waitFor("the check commands' output", () =>
  /^END-OF-CHECK/m.test(document.querySelector("#screen_container > div").innerText), 60);
const out = await screen();
say("::group::Visible VM output");
say(out.replace(/\s+$/gm, "").trim());
say("::endgroup::");
await page.screenshot({ path: `${shots}/running.png` });

const frames = await page.evaluate(() => window.polylab.frames);
say(`frames through the hub: ${JSON.stringify(frames)}`);
for (const name of [visible, ...hidden]) {
  if (!(frames[name] > 0)) await fail(`no network frames from ${name}`);
}

const ip = out.match(/inet (\d+\.\d+\.\d+\.\d+)\//);
if (!ip) await fail(`${visible} has no IPv4 address on eth0`);
const section = (from, to) => out.split(from)[1]?.split(to)[0] || "";
const neighbours = new Set([...section("NEIGH:", "ID:").matchAll(/(\d+\.\d+\.\d+\.\d+) /g)].map(m => m[1]));
neighbours.delete(ip[1]);
say(`${visible} is ${ip[1]}; neighbours seen: ${[...neighbours].join(", ") || "none"}`);
if (neighbours.size < 2) await fail(`${visible} saw ${neighbours.size} other host(s) on the network; expected at least 2`);
const emailHex = Buffer.from(email.toLowerCase()).toString("hex");
const id = section("ID:", "END-OF-CHECK");
if (!id.includes(email.toLowerCase()) && !id.includes(emailHex)) await fail(`the identity file does not hold ${email}`);

say(`page test passed: ${vms.length} VMs booted, traffic flows between them, identity reached the guests`);
await browser.close();
