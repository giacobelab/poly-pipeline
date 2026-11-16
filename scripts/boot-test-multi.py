#!/usr/bin/env python3
"""Boot every VM of a multi-VM lab in QEMU on one shared network and check
that the lab comes up the way the page needs it to.

  boot-test-multi.py --images DIR [--email ADDR] [--log-dir DIR] [--kvm]

DIR holds vms.json and <vm>.bzImage / <vm>.rootfs.cpio.gz (scripts/group.py).
Each VM gets the same PolyLab identity on its kernel command line, as the page
gives them, and an NE2000 PCI card (the card v86 emulates). The cards are
joined by a small hub in this script (QEMU's socket network backend), which
passes every frame to every other VM, like the page does.

Checks:
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
import argparse, datetime, functools, json, os, re, shlex, socket, struct, sys, threading, time
import pexpect

print = functools.partial(print, flush=True)
# net.ifnames=0: keep eth0 (udev would rename it to enp0s3), as the page does.
BASE_CMDLINE = "console=ttyS0 panic=-1 tsc=reliable mitigations=off random.trust_cpu=on net.ifnames=0"
STAGE = ["starting"]


def fail(msg, vm=None):
    if vm is not None:
        print("::group::Last console output")
        print((vm.before or "")[-3000:])
        print("::endgroup::")
    print(f"::error::multi-VM boot test failed while {STAGE[0]}: {msg}")
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


def login(vm, name, timeout):
    STAGE[0] = f"waiting for {name} to reach a login prompt"
    i = vm.expect([r"login: ?$", r"login: ", r"Kernel panic[^\r\n]*", pexpect.EOF, pexpect.TIMEOUT,
                   r"Initramfs unpacking failed[^\r\n]*"], timeout=timeout)
    if i == 5:
        fail(f"{name}: the image does not fit in its memory ({vm.after.strip()}); "
             f"raise this VM's memory_mb in polyci.json", vm)
    if i > 1:
        fail(f"{name}: {['', '', 'kernel panic', 'QEMU exited', 'no login prompt'][i]}", vm)
    STAGE[0] = f"logging in to {name}"
    vm.sendline("root")
    if vm.expect([r"[#$] ?$", r"[Pp]assword: ?", pexpect.TIMEOUT], timeout=60) == 1:
        vm.sendline("")
        if vm.expect([r"[#$] ?$", pexpect.TIMEOUT], timeout=60) != 0:
            fail(f"{name}: root login needs a password", vm)
    # Split so the echoed command itself does not match the prompt.
    vm.sendline("stty cols 200 2>/dev/null; PS1='READ''Y> '")
    vm.expect("READY> ", timeout=30)


def run(vm, name, command, timeout=60):
    """Run one shell command on a VM's serial console and return its output."""
    marker = f"__END_{int(time.time() * 1000) % 100000}__"
    vm.sendline(f"{command}; echo {marker}")
    try:
        vm.expect(f"\n{marker}", timeout=timeout)
    except pexpect.TIMEOUT:
        fail(f"{name}: no answer to {command!r}", vm)
    out = vm.before.split("\n", 1)[1] if "\n" in vm.before else ""
    vm.expect("READY> ", timeout=30)
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out.replace("\r", ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True)
    ap.add_argument("--email", default="ci-test@psu.edu")
    ap.add_argument("--log-dir", default=".")
    ap.add_argument("--kvm", action="store_true")
    ap.add_argument("--boot-timeout", type=int, default=600)
    ap.add_argument("--settle", type=int, default=30, help="seconds of lab traffic before the network checks")
    a = ap.parse_args()

    vms = json.load(open(os.path.join(a.images, "vms.json")))
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
        login(procs[v["name"]], v["name"], a.boot_timeout)
        print(f"{v['name']}: logged in after {int(time.time() - t0)}s")
        info = run(procs[v["name"]], v["name"], "hostname; ip -4 -o addr show eth0; free -m | head -2")
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
            fail(f"no network frames from {name}")

    visible = next(v["name"] for v in vms if v["visible"])
    vm = procs[visible]
    STAGE[0] = f"checking {visible}"
    addr = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", run(vm, visible, "ip -4 -o addr show eth0"))
    if not addr:
        fail(f"{visible} has no IPv4 address on eth0")
    neigh = set(re.findall(r"^(\d+\.\d+\.\d+\.\d+) ", run(vm, visible, "ip neigh show dev eth0"), re.M)) - {addr[1]}
    print(f"{visible} is {addr[1]}; neighbours: {', '.join(sorted(neigh)) or 'none'}")
    if len(neigh) < 2:
        fail(f"{visible} saw {len(neigh)} other host(s) on the network; expected at least 2")

    ident = run(vm, visible, "cat /var/lib/snort-lab/identity 2>/dev/null || echo NO-IDENTITY-FILE")
    if "NO-IDENTITY-FILE" in ident:
        print("no /var/lib/snort-lab/identity on this lab; identity check skipped")
    elif a.email.lower() not in ident and a.email.lower().encode().hex() not in ident:
        fail(f"/var/lib/snort-lab/identity does not hold {a.email}: {ident.strip()[:200]}")
    else:
        print("identity file holds the email")

    if "yes" in run(vm, visible, "command -v tcpdump >/dev/null && echo yes || echo no"):
        STAGE[0] = f"capturing traffic on {visible}"
        cap = run(vm, visible, "timeout 20 tcpdump -n -l -i eth0 -c 60 ip 2>/dev/null | head -60", timeout=60)
        sources = set(re.findall(r" IP (\d+\.\d+\.\d+\.\d+)\.?\d* > ", cap)) - {addr[1]}
        print(f"::group::tcpdump on {visible} ({len(sources)} sources)")
        print(cap.strip())
        print("::endgroup::")
        if len(sources) < 3:
            fail(f"tcpdump on {visible} saw packets from {len(sources)} other address(es); expected at least 3")

    for p in procs.values():
        p.terminate(force=True)
    print(f"multi-VM boot test passed: {len(vms)} VMs up, traffic flows, identity reached the guests")


if __name__ == "__main__":
    main()
