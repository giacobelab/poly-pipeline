#!/usr/bin/env python3
"""Boot a lab's images in QEMU and check them like a student would.

  boot-test.py --images DIR --flavor F [options]     (DIR/vms.json lists the VMs)
  boot-test.py --kernel K --initrd I --flavor F [options]     (one VM)

One VM (flavors v31 and generic):
  1. the kernel boots and reaches a login
  2. root's .profile runs install.sh, which asks for an email and confirmation
  3. the installer logs in as bandit1 and shows the level banner
  4. bandit1's answer (inhere.txt) matches the value derived from the email,
     the guest date and the lab's answer scheme
  5. `nextlevel` walks through every level up to the last one

--flavor picks how much is checked:
  v31      PolyBandit 3.1 (seed-v1 answers, exercise code, staged level readiness)
  generic  any PolyLinux lab: the installer logs in as the first user, and
           nextlevel reaches every level's user (checked with whoami); the
           user names come from --users, e.g. level{n}

  netsec-snort  several VMs on one network (checks below)

--scenario ARCHIVE (generic flavor) tests a lab whose values come from the
PolyLab server: the archive (polylab_server.py archive) is appended to the
image as the page does, the installer must not ask for an email, and the first
level's README must name the archive's participant.

Several VMs (flavor netsec-snort, --images with vms.json listing them):
each VM gets the same PolyLab identity on its kernel command line, as the page
gives them, and an NE2000 PCI card (the card v86 emulates). The cards are
joined by a small hub in this script (QEMU's socket network backend), which
passes every frame to every other VM, like the page does.
  1. every VM boots to a login prompt; each is logged in as root and shows
     its addresses and memory
  2. the hub carries frames from every VM
  3. the visible VM has an IPv4 address on eth0 and, after some traffic, has
     seen at least two other hosts (ip neigh)
  4. the identity file the lab writes (/var/lib/snort-lab/identity) holds the
     email, when the lab writes one
  5. if the visible VM has tcpdump: it captures packets from at least three
     other addresses
"""
import argparse, base64, datetime, functools, gzip, hashlib, json, os, re, shlex, socket, struct, sys
import tempfile, threading, time
import pexpect

# Flush every line so progress shows up live in the Actions log.
print = functools.partial(print, flush=True)

EMAIL = "ci-test@psu.edu"
SYSTEM_PASSWORD = "systemPassword"      # installer default
LEVEL_PASSWORD_ROOT = "levelPassword"   # installer default

FLAVORS = {
    "v31": {
        "banner": "PolyLinux: PolyBandit",
        "date_cmd": 'date +"%Y-%m-%d"',
        "readme_marker": "Exercise code:",
    },
    "generic": {},
    "netsec-snort": {"several": True},
}

STAGE = ["starting"]


def fail(msg, tail=""):
    # Annotations keep only the first line of ::error::, so keep it one line and
    # print the console tail as ordinary (collapsible) log lines.
    if tail:
        print("::group::Last console output")
        print(tail)
        print("::endgroup::")
    print(f"::error::boot test failed while {STAGE[0]}: {msg}")
    sys.exit(1)


def expected_answer(flavor, email, guest_date, level):
    """Answer for one level, computed independently of the guest's scripts."""
    # v31: level_seed_v1 in polylinux-common.sh, answer_token in resources.sh
    fields = ["polylinux-seed-v1", "polybandit", email.strip().lower(), guest_date,
              SYSTEM_PASSWORD, f"{LEVEL_PASSWORD_ROOT}{level}", str(level)]
    seed = hashlib.sha256("".join(f + "\0" for f in fields).encode()).hexdigest()
    answer_hex = hashlib.sha256(f"{seed}:answer".encode()).hexdigest()
    b64 = base64.b64encode(answer_hex.encode()).decode().rstrip("=")
    return b64.replace("+", "-").replace("/", "_")[:20]


def scenario_email(path):
    """The participant's email in a server archive's etc/polylab/scenario.json."""
    data, off = gzip.decompress(open(path, "rb").read()), 0
    while off < len(data):
        head = data[off:off + 110]
        field = lambda i: int(head[6 + 8 * i:14 + 8 * i], 16)
        namesize, size = field(11), field(6)
        name = data[off + 110:off + 110 + namesize - 1].decode()
        body_at = (off + 110 + namesize + 3) & ~3
        if name == "etc/polylab/scenario.json":
            return json.loads(data[body_at:body_at + size])["identity"]["email"]
        off = (body_at + size + 3) & ~3
    sys.exit(f"{path} has no etc/polylab/scenario.json")


def generic(vm, run, a, email=None):
    """Lab-agnostic checks: first user after install, then every level via nextlevel."""
    user = lambda n: a.users.format(n=n)
    STAGE[0] = f"waiting for the installer to log in as {user(1)}"
    # Any shell prompt ("~$ ", "level1$ ", ...) after the installer finishes.
    vm.expect(r"\S*\$ ", timeout=600)
    deadline = time.time() + 120
    while True:
        try:
            who = run("whoami", timeout=30)
        except pexpect.TIMEOUT:
            who = ""
        if who.splitlines()[-1:] == [user(1)]:
            break
        if time.time() > deadline:
            fail(f"expected to be logged in as {user(1)}, whoami says {who!r}")
        time.sleep(3)
    print(f"installer completed; logged in as {user(1)}")
    if email:
        STAGE[0] = f"checking that {user(1)}'s README names the server's participant"
        shown = run("grep -c -F 'Participant: " + email + "' README.txt || true", timeout=30)
        if shown.splitlines()[-1:] == ["0"] or not shown:
            fail(f"README.txt of {user(1)} does not name {email}: the server's values were not used")
        print(f"{user(1)}'s README names {email} (values from the server archive)")

    for n in range(2, a.levels + 1):
        STAGE[0] = f"moving to {user(n)} with nextlevel"
        deadline = time.time() + a.level_ready_timeout
        while True:
            vm.sendline("nextlevel")
            # su may ask for the (empty) password, depending on PAM/shadow setup
            if vm.expect([r"[Pp]assword: ?", r"\S*\$ "], timeout=60) == 0:
                vm.sendline("")
            time.sleep(1)
            who = (run("whoami", timeout=60).splitlines() or [""])[-1]
            if who == user(n):
                break
            if who != user(n - 1):
                fail(f"after nextlevel, whoami says {who!r}; expected {user(n)}")
            if time.time() > deadline:
                fail(f"{user(n)} still not reachable after {a.level_ready_timeout}s")
            time.sleep(3)   # level not ready yet; try again
        print(f"{user(n)} OK")


# ---------------------------------------------------------------------------
# Several VMs (the netsec-snort flavor): every VM in QEMU on one shared network.
# ---------------------------------------------------------------------------
# net.ifnames=0: keep eth0 (udev would rename it to enp0s3), as the page does.
BASE_CMDLINE = "console=ttyS0 panic=-1 tsc=reliable mitigations=off random.trust_cpu=on net.ifnames=0"


def fail_vm(msg, vm=None):
    if vm is not None:
        print("::group::Last console output")
        print((vm.before or "")[-3000:])
        print("::endgroup::")
    print(f"::error::boot test failed while {STAGE[0]}: {msg}")
    sys.exit(1)


class Hub:
    """QEMU -netdev socket,connect=... speaks length-prefixed frames
    (4-byte big-endian length, then the frame). Pass each frame to every
    other connected VM."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.clients, self.frames, self.lock = [], {}, threading.Lock()
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            conn, _ = self.sock.accept()
            with self.lock:
                self.clients.append(conn)
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        def read(n):
            buf = b""
            while len(buf) < n:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError
                buf += chunk
            return buf
        mac = None
        try:
            while True:
                head = read(4)
                frame = read(struct.unpack(">I", head)[0])
                mac = mac or frame[6:12].hex(":")
                with self.lock:
                    self.frames[mac] = self.frames.get(mac, 0) + 1
                    others = [c for c in self.clients if c is not conn]
                for c in others:
                    try:
                        c.sendall(head + frame)
                    except OSError:
                        pass
        except (ConnectionError, OSError):
            with self.lock:
                if conn in self.clients:
                    self.clients.remove(conn)


def identity(email, date):
    code = format(int(date.replace("-", "")), "X")
    return f"polylab_email_hex={email.encode().hex()} polylab_date={date} polylab_code={code}"


def multi_login(vm, name, timeout):
    STAGE[0] = f"waiting for {name} to reach a login prompt"
    i = vm.expect([r"login: ?$", r"login: ", r"Kernel panic[^\r\n]*", pexpect.EOF, pexpect.TIMEOUT,
                   r"Initramfs unpacking failed[^\r\n]*"], timeout=timeout)
    if i == 5:
        fail_vm(f"{name}: the image does not fit in its memory ({vm.after.strip()}); "
             f"raise this VM's memory_mb in polyci.json", vm)
    if i > 1:
        fail_vm(f"{name}: {['', '', 'kernel panic', 'QEMU exited', 'no login prompt'][i]}", vm)
    STAGE[0] = f"logging in to {name}"
    vm.sendline("root")
    if vm.expect([r"[#$] ?$", r"[Pp]assword: ?", pexpect.TIMEOUT], timeout=60) == 1:
        vm.sendline("")
        if vm.expect([r"[#$] ?$", pexpect.TIMEOUT], timeout=60) != 0:
            fail_vm(f"{name}: root login needs a password", vm)
    # Split so the echoed command itself does not match the prompt.
    vm.sendline("stty cols 200 2>/dev/null; PS1='READ''Y> '")
    vm.expect("READY> ", timeout=30)


def multi_run(vm, name, command, timeout=60):
    """Run one shell command on a VM's serial console and return its output."""
    marker = f"__END_{int(time.time() * 1000) % 100000}__"
    vm.sendline(f"{command}; echo {marker}")
    try:
        vm.expect(f"\n{marker}", timeout=timeout)
    except pexpect.TIMEOUT:
        fail_vm(f"{name}: no answer to {command!r}", vm)
    out = vm.before.split("\n", 1)[1] if "\n" in vm.before else ""
    vm.expect("READY> ", timeout=30)
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out.replace("\r", ""))


def multi_main(a, vms):
    """The netsec-snort flavor: all of vms on one network (checks in the module docstring)."""
    date = datetime.date.today().isoformat()
    cmdline = f"{BASE_CMDLINE} {identity(a.email.lower(), date)}"
    hub = Hub()
    print(f"hub on 127.0.0.1:{hub.port}; identity: {a.email} {date}")

    procs = {}
    for n, v in enumerate(vms):
        cmd = ["qemu-system-i386", "-m", f"{v['memory_mb']}M", "-nographic", "-no-reboot",
               "-kernel", os.path.join(a.images, v["bzimage"]), "-initrd", os.path.join(a.images, v["initrd"]),
               "-append", cmdline,
               "-netdev", f"socket,id=n0,connect=127.0.0.1:{hub.port}",
               "-device", f"ne2k_pci,netdev=n0,mac=52:54:00:00:00:{n + 1:02x}"]
        cmd += ["-enable-kvm", "-cpu", "host"] if a.kvm else []
        print(f"{v['name']}: {' '.join(shlex.quote(c) for c in cmd)}")
        vm = pexpect.spawn(cmd[0], cmd[1:], encoding="utf-8", codec_errors="replace", timeout=a.boot_timeout)
        vm.logfile_read = open(os.path.join(a.log_dir, f"boot-{v['name']}.log"), "w")
        procs[v["name"]] = vm

    macs = {v["name"]: f"52:54:00:00:00:{n + 1:02x}" for n, v in enumerate(vms)}
    t0 = time.time()
    for v in vms:
        multi_login(procs[v["name"]], v["name"], a.boot_timeout)
        print(f"{v['name']}: logged in after {int(time.time() - t0)}s")
        info = multi_run(procs[v["name"]], v["name"], "hostname; ip -4 -o addr show eth0; free -m | head -2")
        print(f"::group::{v['name']} ({v['memory_mb']} MB)")
        print(info.strip())
        print("::endgroup::")

    STAGE[0] = "waiting for network traffic from every VM"
    deadline = time.time() + 240
    while time.time() < deadline and not all(hub.frames.get(m, 0) >= 5 for m in macs.values()):
        time.sleep(2)
    STAGE[0] = "letting the lab's traffic flow"
    time.sleep(a.settle)
    print(f"frames through the hub: { {n: hub.frames.get(m, 0) for n, m in macs.items()} }")
    for name, mac in macs.items():
        if not hub.frames.get(mac):
            fail_vm(f"no network frames from {name}")

    visible = next(v["name"] for v in vms if v["visible"])
    vm = procs[visible]
    STAGE[0] = f"checking {visible}"
    addr = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", multi_run(vm, visible, "ip -4 -o addr show eth0"))
    if not addr:
        fail_vm(f"{visible} has no IPv4 address on eth0")
    neigh = set(re.findall(r"^(\d+\.\d+\.\d+\.\d+) ", multi_run(vm, visible, "ip neigh show dev eth0"), re.M)) - {addr[1]}
    print(f"{visible} is {addr[1]}; neighbours: {', '.join(sorted(neigh)) or 'none'}")
    if len(neigh) < 2:
        fail_vm(f"{visible} saw {len(neigh)} other host(s) on the network; expected at least 2")

    ident = multi_run(vm, visible, "cat /var/lib/snort-lab/identity 2>/dev/null || echo NO-IDENTITY-FILE")
    if "NO-IDENTITY-FILE" in ident:
        print("no /var/lib/snort-lab/identity on this lab; identity check skipped")
    elif a.email.lower() not in ident and a.email.lower().encode().hex() not in ident:
        fail_vm(f"/var/lib/snort-lab/identity does not hold {a.email}: {ident.strip()[:200]}")
    else:
        print("identity file holds the email")

    if "yes" in multi_run(vm, visible, "command -v tcpdump >/dev/null && echo yes || echo no"):
        STAGE[0] = f"capturing traffic on {visible}"
        cap = multi_run(vm, visible, "timeout 20 tcpdump -n -l -i eth0 -c 60 ip 2>/dev/null | head -60", timeout=60)
        sources = set(re.findall(r" IP (\d+\.\d+\.\d+\.\d+)\.?\d* > ", cap)) - {addr[1]}
        print(f"::group::tcpdump on {visible} ({len(sources)} sources)")
        print(cap.strip())
        print("::endgroup::")
        if len(sources) < 3:
            fail_vm(f"tcpdump on {visible} saw packets from {len(sources)} other address(es); expected at least 3")

    for p in procs.values():
        p.terminate(force=True)
    print(f"boot test passed: {len(vms)} VMs up, traffic flows, identity reached the guests")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", help="folder with vms.json and the images it names")
    ap.add_argument("--kernel")
    ap.add_argument("--initrd")
    ap.add_argument("--flavor", choices=sorted(FLAVORS), default="generic")
    ap.add_argument("--levels", type=int, default=13)
    ap.add_argument("--users", default="bandit{n}", help="level user name pattern (generic flavor)")
    ap.add_argument("--log", default="boot.log")
    ap.add_argument("--kvm", action="store_true")
    ap.add_argument("--boot-timeout", type=int, default=300)
    ap.add_argument("--level-ready-timeout", type=int, default=300,
                    help="v31: how long nextlevel may keep saying a level is being prepared")
    ap.add_argument("--cmd", help="override the spawned command (for self-testing)")
    ap.add_argument("--scenario", help="server archive to append to --initrd (generic flavor)")
    ap.add_argument("--email", default=EMAIL, help="several VMs: the identity's email")
    ap.add_argument("--log-dir", default=".", help="several VMs: where boot-<vm>.log go")
    ap.add_argument("--settle", type=int, default=30, help="several VMs: seconds of lab traffic before the network checks")
    a = ap.parse_args()
    fl = FLAVORS[a.flavor]
    if a.images:
        vms = json.load(open(os.path.join(a.images, "vms.json")))
        if len(vms) > 1 or fl.get("several"):
            if not fl.get("several"):
                ap.error(f"{a.images}/vms.json lists {len(vms)} VMs; the {a.flavor} flavor boots one")
            if a.scenario:
                ap.error("--scenario works with one VM only (for now)")
            if a.boot_timeout == 300:
                a.boot_timeout = 600
            return multi_main(a, vms)
        a.kernel = os.path.join(a.images, vms[0]["bzimage"])
        a.initrd = os.path.join(a.images, vms[0]["initrd"])
    elif fl.get("several"):
        ap.error(f"the {a.flavor} flavor needs --images (a vms.json with its VMs)")
    email = None
    if a.scenario:
        if a.flavor != "generic":
            ap.error("--scenario works with the generic flavor only")
        email = scenario_email(a.scenario)
        joined = tempfile.NamedTemporaryFile(prefix="initrd-", suffix=".cpio.gz", delete=False)
        for part in (a.initrd, a.scenario):
            with open(part, "rb") as fh:
                joined.write(fh.read())
        joined.close()
        a.initrd = joined.name
        print(f"server archive for {email} appended to the image")

    if a.cmd:
        cmd = a.cmd
    else:
        if not (a.kernel and a.initrd):
            ap.error("--kernel and --initrd are required")
        cmd = " ".join([
            "qemu-system-i386", "-m", "512M", "-nographic", "-no-reboot",
            "-kernel", shlex.quote(a.kernel), "-initrd", shlex.quote(a.initrd),
            "-append", shlex.quote("console=ttyS0 panic=-1"),
            "-netdev", "user,id=n0", "-device", "e1000,netdev=n0",
        ] + (["-enable-kvm", "-cpu", "host"] if a.kvm else []))

    print(f"flavor {a.flavor}; spawning: {cmd}")
    log = open(a.log, "w", encoding="utf-8", errors="replace")
    vm = pexpect.spawn("/bin/sh", ["-c", cmd], encoding="utf-8",
                       codec_errors="replace", timeout=60, dimensions=(50, 200))
    vm.logfile_read = log
    marker = [0]

    ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")

    def run(command, timeout=60):
        """Run a shell command in the guest; return its cleaned output."""
        marker[0] += 1
        start, end = f"__CS{marker[0]}__", f"__CE{marker[0]}__"
        # Markers are split with '' so the echoed command line can't match them.
        vm.sendline(f"echo {start[:4]}''{start[4:]}; {command}; echo {end[:4]}''{end[4:]}")
        # The serial console ends lines with \r\r\n, so allow any number of \r.
        vm.expect(re.escape(start) + r"\r*\n", timeout=timeout)
        vm.expect_exact(end, timeout=timeout)
        return ansi.sub("", vm.before).strip()

    try:
        # 1-2: boot to the installer, logging in as root if a getty is in the way
        t_boot = time.time()
        STAGE[0] = "booting to the installer prompt"
        while True:
            i = vm.expect([r"login: ?", r"[Pp]assword: ?",
                           r"Enter your email address", r"Kernel panic.*", r"Exercise code: \S+"],
                          timeout=a.boot_timeout)
            if i == 0:
                vm.sendline("root")
            elif i == 1:
                vm.sendline("")
            elif i == 2:
                if email:
                    fail("the installer asked for an email although the server's values were appended")
                break
            elif i == 4:
                if not email:
                    fail("the installer did not ask for an email")
                print(f"installer started with the server's values ({vm.after.strip()})")
                break
            else:
                vm.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=5)
                fail("kernel panic during boot", ansi.sub("", vm.after + (vm.before or ""))[-1500:])
        print(f"reached installer after {time.time() - t_boot:.0f}s")
        if not email:
            STAGE[0] = "answering the installer prompts"
            vm.sendline(EMAIL)
            vm.expect(r"\(y/n\)")
            vm.sendline("y")

        if a.flavor == "generic":
            generic(vm, run, a, email)
            print("boot test passed")
            return

        # 3: installer finishes and logs us in as bandit1
        STAGE[0] = "waiting for the installer to log in as bandit1"
        # Fail fast if the installer dies (shell error or its own ERROR:/die
        # message) instead of waiting out the timeout for a banner that won't come.
        i = vm.expect([re.escape(fl["banner"]),
                       r"install\.sh: line \d+: [^\r\n]+",
                       r"ERROR: [^\r\n]+"], timeout=600)
        if i:
            fail(f"installer error: {vm.after.strip()}")
        vm.expect(r"Level\s*:\s*bandit1\b")
        print("installer completed; logged in as bandit1")
        time.sleep(2)

        STAGE[0] = "checking bandit1"
        who = run("whoami")
        if who != "bandit1":
            fail(f"expected to be bandit1, got {who!r}")

        # 4: level 1 answer matches what the generator should have written
        guest_date = run(fl["date_cmd"])
        flag = run("cat inhere.txt")
        expected = expected_answer(a.flavor, EMAIL, guest_date, 1)
        if flag != expected:
            fail(f"bandit1 answer mismatch: got {flag!r}, expected {expected!r} "
                 f"(date {guest_date})")
        print(f"bandit1 answer OK ({flag})")
        if a.flavor == "v31":
            code = format(int(guest_date.replace("-", "")), "X")
            if f"Exercise code: {code}" not in run("cat README.txt"):
                fail(f"README.txt does not show exercise code {code} for {guest_date}")
            print(f"exercise code OK ({code})")

        # 5: walk the levels with nextlevel
        for n in range(2, a.levels + 1):
            STAGE[0] = f"moving to bandit{n} with nextlevel"
            level = rf"Level\s*:\s*bandit{n}\b"
            su_error = r"su: [^\r\n]+"
            preparing = r"is still being prepared"
            broken = r"could not be prepared[^\r\n]*"
            deadline = time.time() + a.level_ready_timeout
            while True:
                vm.sendline("nextlevel")
                # su may prompt for the (empty) password depending on PAM/shadow setup
                i = vm.expect([r"[Pp]assword: ?", level, su_error, preparing, broken], timeout=60)
                if i == 0:
                    vm.sendline("")
                    i = vm.expect([level, su_error], timeout=60) + 1
                if i == 1:
                    break
                if i == 2:
                    fail(f"nextlevel failed: {vm.after.strip()}")
                if i == 4:
                    fail(f"bandit{n} {vm.after.strip()}")
                if time.time() > deadline:
                    fail(f"bandit{n} was still being prepared after {a.level_ready_timeout}s")
                time.sleep(3)   # i == 3: level not ready yet; try again
            readme = run("cat README.txt")
            if fl["readme_marker"] not in readme:
                fail(f"bandit{n} README.txt is missing {fl['readme_marker']!r}")
            print(f"bandit{n} OK")
        vm.sendline("nextlevel")  # past the last level: should just refuse
        time.sleep(1)
        print("boot test passed")
    except pexpect.TIMEOUT:
        tail = ansi.sub("", vm.before or "")[-1500:]
        fail("timed out (full console in the boot-log artifact)", tail)
    except pexpect.EOF:
        fail("VM exited unexpectedly (see the boot-log artifact)", ansi.sub("", vm.before or "")[-1500:])
    finally:
        vm.terminate(force=True)
        log.close()


if __name__ == "__main__":
    main()
