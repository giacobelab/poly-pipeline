// Play an assembled lab page in headless Chrome, as a student would. The same
// page (web/index.html) serves every lab, so this one test covers them all.
//
//   node page-test.mjs <url> <screenshot-dir> [--users level{n}] [--email you@psu.edu]
//
// It reads the page's build.json and images/vms.json and checks what applies.
//
// A lab with one VM:
//   - the page loads, v86 boots, and auto-login reaches the installer
//   - in-VM email (no "server" in build.json): the installer asks, the email
//     is typed (with a real Shift+2 for "@") and confirmed
//   - server lab: the email goes into the page's start form, the installer
//     must not ask for it, and the first level's README must name it (serve
//     the site with poly-server's polylab_server.py for that)
//   - the installer logs in as the first level's user, and nextlevel reaches
//     the second level's user (checked with whoami, for any PolyLinux lab)
//
// A lab with several VMs:
//   1. enter an email and start; the visible VM boots and is logged in as root
//   2. every hidden VM (each in its own Web Worker) reaches its login prompt
//   3. the page's hub carries frames from every VM
//   4. on the visible VM: eth0 has an address, it has seen at least two other
//      hosts on the segment (ip neigh), and the identity file the lab writes
//      holds the email that was entered
import puppeteer from "puppeteer-core";
import fs from "node:fs";

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const i = args.indexOf(name);
  if (i < 0) return fallback;
  const value = args[i + 1];
  args.splice(i, 2);
  return value;
};
const pattern = option("--users", "bandit{n}");
const email = option("--email", "ci-page@psu.edu").toLowerCase();
const [url, shots = "page-shots"] = args;
if (!url) { console.log("usage: node page-test.mjs <url> <screenshot-dir> [--users P] [--email E]"); process.exit(2); }
const user = n => pattern.replace("{n}", String(n));
fs.mkdirSync(shots, { recursive: true });
const BOOT_S = Number(process.env.PAGE_BOOT_TIMEOUT || 420);
const chrome = process.env.CHROME_PATH || "/usr/bin/google-chrome";

const browser = await puppeteer.launch({ executablePath: chrome, headless: true,
                                         args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900 });
const errors = [];
page.on("pageerror", e => { errors.push(String(e)); console.log("page error:", e.message); });
page.on("console", m => { if (m.type() === "error") console.log("console error:", m.text()); });

const t0 = Date.now();
const secs = () => `${((Date.now() - t0) / 1000).toFixed(0)}s`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const screenText = () => page.$eval("#screen_container > div", el => el.innerText);

class Failure extends Error {}
async function waitForScreen(re, ms, what) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (re.test(await screenText())) return;
    await sleep(1000);
  }
  throw new Failure(`timed out waiting for ${what}`);
}
async function waitFor(what, test, seconds, arg) {
  const start = Date.now();
  while ((Date.now() - start) / 1000 < seconds) {
    if (await page.evaluate(test, arg)) { console.log(`${what} after ${Math.round((Date.now() - start) / 1000)}s`); return; }
    const vmErrors = await page.evaluate(() => window.polylab.errors);
    if (vmErrors.length) throw new Failure(`VM error: ${vmErrors.join("; ")}`);
    await sleep(1000);
  }
  throw new Failure(`timed out after ${seconds}s waiting for: ${what}`);
}

// v86 reads physical keys, and puppeteer types shifted characters without a
// Shift keypress, so press Shift + the base key ourselves (US layout).
const SHIFTED = {
  "!": "Digit1", "@": "Digit2", "#": "Digit3", "$": "Digit4", "%": "Digit5",
  "^": "Digit6", "&": "Digit7", "*": "Digit8", "(": "Digit9", ")": "Digit0",
  "_": "Minus", "+": "Equal", "{": "BracketLeft", "}": "BracketRight",
  "|": "Backslash", ":": "Semicolon", "\"": "Quote", "<": "Comma",
  ">": "Period", "?": "Slash", "~": "Backquote",
};
async function typeText(text) {
  for (const ch of text) {
    const code = SHIFTED[ch] || (/[A-Z]/.test(ch) ? `Key${ch}` : null);
    if (code) {
      await page.keyboard.down("Shift");
      await page.keyboard.press(code);
      await page.keyboard.up("Shift");
    } else if (ch === "\n") {
      await page.keyboard.press("Enter");
    } else {
      await page.keyboard.type(ch);
    }
    await sleep(25);
  }
}
// Ask the guest who we are. The marker is built by the shell, so the typed
// command line ("__W$(whoami)__") can't match the output pattern.
let probe = 0;
async function whoami() {
  probe++;
  await typeText(`clear; echo __W${probe}_$(whoami)__\n`);
  const re = new RegExp(`__W${probe}_([A-Za-z0-9_.-]+)__`);
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    const m = re.exec(await screenText());
    if (m) return m[1];
    await sleep(500);
  }
  return "";
}

// ---- one VM: play the first two levels ------------------------------------
async function oneVM(server) {
  if (server) {
    await page.waitForSelector("#start:not([hidden])", { timeout: 30000 });
    await page.type("#email", email.replace(/^ci-page/, "CI-Page") + " ");
    await page.click("#go");
    console.log("server lab: email entered in the page's start form");
    await waitForScreen(/Exercise code|Enter your email/, BOOT_S * 1000, "the installer (boot + auto-login)");
    if (/Enter your email/.test(await screenText()))
      throw new Failure("the installer asked for an email although the server's values were appended");
    console.log(`installer started with the server's values after ${secs()}`);
    await page.click("#screen_container");
  } else {
    await waitForScreen(/Enter your email/, BOOT_S * 1000, "the installer prompt (boot + auto-login)");
    console.log(`installer prompt on screen after ${secs()}`);
    await page.click("#screen_container");
    await typeText(`${email}\n`);
    await waitForScreen(new RegExp(`${email.replace(/[.@]/g, "\\$&")}.*\\(y/n\\)`), 30000, "the email confirmation");
    await typeText("y\n");
  }

  // The installer runs, then logs in as the first user and shows its README.
  let who = "";
  const loginBy = Date.now() + 300000;
  while (Date.now() < loginBy) {
    await sleep(5000);
    if (!/\$ ?$/m.test(await screenText())) continue;   // no shell prompt yet
    who = await whoami();
    if (who === user(1)) break;
  }
  if (who !== user(1)) throw new Failure(`installer did not log in as ${user(1)} (whoami: ${who || "no answer"})`);
  console.log(`logged in as ${user(1)} after ${secs()}`);
  if (server) {
    await typeText(`clear; grep -c -F 'Participant: ${email}' README.txt\n`);
    await sleep(2000);
    if (!/^\s*[1-9]\d*\s*$/m.test(await screenText()))
      throw new Failure(`the first level's README does not name ${email}: the server's values were not used`);
    console.log(`${user(1)}'s README names ${email} (values from the server)`);
  }
  await page.screenshot({ path: `${shots}/first-level.png` });

  const nextBy = Date.now() + 240000;
  for (;;) {
    await typeText("clear\n");
    await sleep(1500);   // let clear finish, or keystrokes get split
    await typeText("nextlevel\n");
    await sleep(4000);
    who = await whoami();
    if (who === user(2)) break;
    if (who !== user(1)) throw new Failure(`after nextlevel, whoami says ${who || "nothing"}`);
    if (Date.now() > nextBy) throw new Failure(`${user(2)} still not reachable after 240s`);
    await sleep(3000);   // level not ready yet; retry
  }
  console.log(`nextlevel reached ${user(2)} after ${secs()}`);
  return "one VM booted, the installer ran, and nextlevel works";
}

// ---- several VMs: all up, on one network, with the identity ------------------
async function severalVMs(vms) {
  const visible = (vms.find(v => v.visible) || vms[0]).name;
  const hidden = vms.map(v => v.name).filter(n => n !== visible);
  console.log(`VMs: ${visible} (visible), ${hidden.join(", ")} (hidden, in workers)`);
  await page.waitForSelector("#start:not([hidden])", { timeout: 30000 });
  await page.type("#email", email);
  await page.click("#go");
  console.log(`started with ${email}`);

  await waitFor(`${visible} logged in`, () => document.getElementById("status-text").textContent.startsWith("Ready"), BOOT_S);
  for (const name of hidden) {
    await waitFor(`${name} at its login prompt`, n => window.polylab.state[n] === "running", BOOT_S, name);
  }
  // Wait until every VM has sent some traffic (the lab's programs start after
  // the login prompt, and Python starts slowly in v86), then let it flow a bit.
  await waitFor("network traffic from every VM", names =>
    names.every(n => (window.polylab.frames[n] || 0) >= 5), Number(process.env.PAGE_TRAFFIC_TIMEOUT || 240),
    [visible, ...hidden]);
  await sleep(10000);

  await page.evaluate(() => window.emulator.keyboard_send_text(
    "clear; echo IP:; ip -4 -o addr show eth0; echo NEIGH:; ip neigh show dev eth0; " +
    "echo ID:; head -c 300 /var/lib/snort-lab/identity; echo; echo END-OF-CHECK\n"));
  await waitFor("the check commands' output", () =>
    /^END-OF-CHECK/m.test(document.querySelector("#screen_container > div").innerText), 60);
  const out = await screenText();
  console.log("::group::Visible VM output");
  console.log(out.replace(/\s+$/gm, "").trim());
  console.log("::endgroup::");
  await page.screenshot({ path: `${shots}/running.png` });

  const frames = await page.evaluate(() => window.polylab.frames);
  console.log(`frames through the hub: ${JSON.stringify(frames)}`);
  for (const name of [visible, ...hidden]) {
    if (!(frames[name] > 0)) throw new Failure(`no network frames from ${name}`);
  }
  const ip = out.match(/inet (\d+\.\d+\.\d+\.\d+)\//);
  if (!ip) throw new Failure(`${visible} has no IPv4 address on eth0`);
  const section = (from, to) => out.split(from)[1]?.split(to)[0] || "";
  const neighbours = new Set([...section("NEIGH:", "ID:").matchAll(/(\d+\.\d+\.\d+\.\d+) /g)].map(m => m[1]));
  neighbours.delete(ip[1]);
  console.log(`${visible} is ${ip[1]}; neighbours seen: ${[...neighbours].join(", ") || "none"}`);
  if (neighbours.size < 2) throw new Failure(`${visible} saw ${neighbours.size} other host(s) on the network; expected at least 2`);
  const id = section("ID:", "END-OF-CHECK");
  if (!id.includes(email) && !id.includes(Buffer.from(email).toString("hex")))
    throw new Failure(`the identity file does not hold ${email}`);
  return `${vms.length} VMs booted, traffic flows between them, identity reached the guests`;
}

let ok = false;
try {
  await page.goto(url, { waitUntil: "load" });
  const build = await page.evaluate(() => fetch("build.json").then(r => r.ok ? r.json() : {}).catch(() => ({})));
  const vms = await page.evaluate(() => window.polylab && window.polylab.vms.length ? window.polylab.vms
    : fetch("images/vms.json").then(r => r.json()));
  console.log(`page: ${build.title || "(no title)"}, ${vms.length} VM(s)` +
              `${build.server ? ", values from the PolyLab server" : ""}${build.identity ? `, identity ${build.identity}` : ""}`);
  const done = vms.length > 1 ? await severalVMs(vms) : await oneVM(build.server === true);
  ok = errors.length === 0;
  if (ok) console.log(`page test passed: ${done}`);
  else console.log(`::error::page test: JavaScript errors on the page: ${errors.join(" | ")}`);
} catch (e) {
  console.log("::group::Screen at failure");
  console.log(await screenText().catch(() => "(no screen)"));
  console.log("::endgroup::");
  try {
    const info = await page.evaluate(() => ({ state: window.polylab?.state, frames: window.polylab?.frames,
      errors: window.polylab?.errors, serial: Object.fromEntries(Object.entries(window.polylab?.serial || {})
        .map(([k, v]) => [k, v.slice(-15)])) }));
    if (info.state && Object.keys(info.state).length > 1) {
      console.log("::group::Page state at failure");
      console.log(JSON.stringify(info, null, 2));
      console.log("::endgroup::");
    }
  } catch (_) {}
  console.log(`::error::page test failed: ${e instanceof Failure ? e.message : e.stack || e}`);
} finally {
  await page.screenshot({ path: `${shots}/final.png` }).catch(() => {});
  await browser.close();
}
if (!ok) console.log("page test FAILED");
process.exit(ok ? 0 : 1);
