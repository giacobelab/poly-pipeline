// Play an assembled PolyCI lab page in headless Chrome like a student:
// page loads, v86 boots, auto-login reaches the installer, an email (typed
// with a real Shift+2 for "@") is accepted, the installer logs in as the
// first level's user, and nextlevel reaches the second level's user.
// Users are checked with whoami, so this works for any PolyLinux lab.
// For a lab whose values come from the PolyLab server (build.json "server"),
// the email goes into the page's start form instead, the installer must not
// ask for it, and the first level's README must name it. Serve the site with
// poly-server's polylab_server.py for that.
// Usage: node page-test.mjs <url> <screenshot-dir> [user-pattern, e.g. "level{n}"]
import puppeteer from "puppeteer-core";
import fs from "node:fs";

const url = process.argv[2];
const shots = process.argv[3] || ".";
const pattern = process.argv[4] || "bandit{n}";
const user = n => pattern.replace("{n}", String(n));
fs.mkdirSync(shots, { recursive: true });
const chrome = process.env.CHROME_PATH || "/usr/bin/google-chrome";

const browser = await puppeteer.launch({
  executablePath: chrome,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1100, height: 760 });
const errors = [];
page.on("pageerror", e => errors.push(String(e)));

const t0 = Date.now();
const secs = () => `${((Date.now() - t0) / 1000).toFixed(0)}s`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const screenText = () => page.$eval("#screen_container > div", el => el.innerText);
async function waitFor(re, ms, what) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (re.test(await screenText())) return;
    await sleep(1000);
  }
  throw new Error(`timed out waiting for ${what}`);
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

let ok = false;
try {
  await page.goto(url, { waitUntil: "load" });
  const server = await page.evaluate(async () => {
    const r = await fetch("build.json");
    return r.ok && (await r.json()).server === true;
  }).catch(() => false);
  if (server) {
    await page.waitForSelector("#start:not([hidden])", { timeout: 30000 });
    await page.type("#email", "CI-Page@psu.edu ");
    await page.click("#go");
    console.log("server lab: email entered in the page's start form");
    await waitFor(/Exercise code|Enter your email/, 240000, "the installer (boot + auto-login)");
    if (/Enter your email/.test(await screenText()))
      throw new Error("the installer asked for an email although the server's values were appended");
    console.log(`installer started with the server's values after ${secs()}`);
    await page.click("#screen_container");
  } else {
    await waitFor(/Enter your email/, 240000, "the installer prompt (boot + auto-login)");
    console.log(`installer prompt on screen after ${secs()}`);
    await page.click("#screen_container");
    await typeText("ci-page@psu.edu\n");
    await waitFor(/ci-page@psu\.edu.*\(y\/n\)/, 30000, "the email confirmation");
    await typeText("y\n");
  }

  // Installer runs, then logs in as the first user and shows its README.
  let who = "";
  const loginBy = Date.now() + 300000;
  while (Date.now() < loginBy) {
    await sleep(5000);
    if (!/\$ ?$/m.test(await screenText())) continue;   // no shell prompt yet
    who = await whoami();
    if (who === user(1)) break;
  }
  if (who !== user(1)) throw new Error(`installer did not log in as ${user(1)} (whoami: ${who || "no answer"})`);
  console.log(`logged in as ${user(1)} after ${secs()}`);
  if (server) {
    await typeText("clear; grep -c -F 'Participant: ci-page@psu.edu' README.txt\n");
    await sleep(2000);
    if (!/^\s*[1-9]\d*\s*$/m.test(await screenText()))
      throw new Error("the first level's README does not name ci-page@psu.edu: the server's values were not used");
    console.log(`${user(1)}'s README names ci-page@psu.edu (values from the server)`);
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
    if (who !== user(1)) throw new Error(`after nextlevel, whoami says ${who || "nothing"}`);
    if (Date.now() > nextBy) throw new Error(`${user(2)} still not reachable after 240s`);
    await sleep(3000);   // level not ready yet; retry
  }
  console.log(`nextlevel reached ${user(2)} after ${secs()}`);
  ok = errors.length === 0;
  if (!ok) console.log(`::error::page test: JavaScript errors on the page: ${errors.join(" | ")}`);
} catch (e) {
  console.log("::group::Screen at failure");
  console.log(await screenText().catch(() => "(no screen)"));
  console.log("::endgroup::");
  console.log(`::error::page test failed: ${e.message}`);
} finally {
  await page.screenshot({ path: `${shots}/final.png` }).catch(() => {});
  await browser.close();
}
console.log(ok ? "page test passed" : "page test FAILED");
process.exit(ok ? 0 : 1);
