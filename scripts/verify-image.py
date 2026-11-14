#!/usr/bin/env python3
"""Static checks on a joined image (baseline rootfs.cpio.gz + lab.cpio.gz).

  verify-image.py --packaged packaged.rootfs.cpio.gz --base rootfs.cpio.gz
                  --lab lab.cpio.gz --manifest lab-manifest.txt
                  [--required required-commands.txt] [--command NAME ...]

1. packaged == base bytes followed by lab bytes (the base is untouched)
2. after unpacking both archives in order (as the kernel does), every file in
   the lab manifest is in /root with the same mode and SHA-256
3. every required command exists in bin, sbin, usr/bin or usr/sbin
"""
import argparse, gzip, hashlib, stat, sys

errors = []


def err(msg):
    errors.append(msg)
    print(f"::error::{msg}")


def unpack(blob):
    """Merged view of concatenated newc archives: later entries win."""
    data = gzip.decompress(blob)  # handles several concatenated gzip members
    off, view = 0, {}
    while off < len(data):
        if data[off:off + 6] not in (b"070701", b"070702"):
            off += 1
            continue
        h = data[off:off + 110]
        f = lambda i: int(h[6 + 8 * i:14 + 8 * i], 16)
        mode, size, namesize = f(1), f(6), f(11)
        name = data[off + 110:off + 110 + namesize - 1].decode().lstrip("./").lstrip("/")
        off = (off + 110 + namesize + 3) & ~3
        body = data[off:off + size]
        off = (off + size + 3) & ~3
        if name != "TRAILER!!!":
            view[name] = (mode, body)
    return view


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packaged", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--lab", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--required")
    ap.add_argument("--command", action="append", default=[])
    a = ap.parse_args()

    packaged, base, lab = (open(p, "rb").read() for p in (a.packaged, a.base, a.lab))
    if packaged != base + lab:
        err("packaged rootfs is not exactly the baseline rootfs followed by lab.cpio.gz")
    view = unpack(packaged)

    checked = 0
    for line in open(a.manifest):
        mode_s, path, digest = line.rstrip("\n").rsplit(" ", 2)
        name = path.lstrip("/")
        if name not in view:
            err(f"missing {path}")
            continue
        mode, body = view[name]
        if stat.filemode(mode) != mode_s:
            err(f"{path} has mode {stat.filemode(mode)}, expected {mode_s}")
        if hashlib.sha256(body).hexdigest() != digest:
            err(f"{path} content differs from the lab commit")
        checked += 1
    if "root" in view and stat.filemode(view["root"][0]) != "drwxr-xr-x":
        err(f"/root has mode {stat.filemode(view['root'][0])}, expected drwxr-xr-x")

    commands = list(a.command)
    if a.required:
        commands += [c.strip() for c in open(a.required, encoding="utf-8", errors="replace")
                     if c.strip() and not c.lstrip().startswith("#")]
    for c in dict.fromkeys(commands):
        if not any(f"{d}/{c}" in view for d in ("bin", "sbin", "usr/bin", "usr/sbin")):
            err(f"missing command in image: {c}")

    if errors:
        print(f"Image verification FAILED with {len(errors)} problem(s).")
        sys.exit(1)
    print(f"Image verification passed: base untouched, {checked} lab files match their commit, "
          f"{len(dict.fromkeys(commands))} commands present.")


if __name__ == "__main__":
    main()
