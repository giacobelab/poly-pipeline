#!/usr/bin/env python3
"""Check a freshly built baseline before it is published.

  check-baseline.py --config-set DIR --final-config FILE --rootfs rootfs.cpio.gz
                    [--required required-commands.txt] [--common-dir DIR]

1. Every option the config set asks for (BR2_*=y in its defconfig and
   features.fragment, the two files 02-build-baseline.sh applies, plus the
   shared files the set's `includes` lists from --common-dir, unless the
   set's own files mention that option) is still =y in Buildroot's final .config. Buildroot drops
   options whose dependencies are not met without saying so; for example
   BR2_PACKAGE_BASH needs BR2_PACKAGE_BUSYBOX_SHOW_OTHERS=y.
2. Every command in required-commands.txt exists in the rootfs.
"""
import argparse, gzip, re, sys
from pathlib import Path

ASK = re.compile(r"^(BR2_[A-Z0-9_]+)=y\s*$")
HAVE = re.compile(r"^(BR2_[A-Z0-9_]+)=y\s*$")


def requested(config_set, common_dir=None):
    wanted = {}
    own = set()
    for f in sorted(Path(config_set).iterdir()):
        # 02-build-baseline.sh applies only these two files to Buildroot.
        if f.name in ("defconfig", "features.fragment"):
            for line in f.read_text(errors="replace").splitlines():
                own.update(re.findall(r"BR2_[A-Z0-9_]+", line))
                m = ASK.match(line.strip().replace("\r", ""))
                if m:
                    wanted.setdefault(m.group(1), f.name)
    inc = Path(config_set) / "includes"
    if common_dir and inc.exists():
        for raw in inc.read_text().splitlines():
            name = raw.split("#")[0].strip()
            if not name:
                continue
            for line in (Path(common_dir) / name).read_text(errors="replace").splitlines():
                m = ASK.match(line.strip().replace("\r", ""))
                if m and m.group(1) not in own:
                    wanted.setdefault(m.group(1), f"config-common/{name}")
    return wanted


def rootfs_names(path):
    data = gzip.decompress(Path(path).read_bytes())
    off, names = 0, set()
    while off < len(data):
        if data[off:off + 6] not in (b"070701", b"070702"):
            off += 1
            continue
        h = data[off:off + 110]
        f = lambda i: int(h[6 + 8 * i:14 + 8 * i], 16)
        size, namesize = f(6), f(11)
        names.add(data[off + 110:off + 110 + namesize - 1].decode().lstrip("./").lstrip("/"))
        off = (off + 110 + namesize + 3) & ~3
        off = (off + size + 3) & ~3
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-set", required=True)
    ap.add_argument("--final-config", required=True)
    ap.add_argument("--rootfs", required=True)
    ap.add_argument("--required")
    ap.add_argument("--common-dir", help="the builder's config-common/ (for sets with an includes file)")
    a = ap.parse_args()
    problems = 0

    wanted = requested(a.config_set, a.common_dir)
    final = {m.group(1) for line in Path(a.final_config).read_text().splitlines()
             if (m := HAVE.match(line.strip()))}
    dropped = sorted(k for k in wanted if k not in final)
    for k in dropped:
        print(f"::error::{k}=y is in {wanted[k]} but Buildroot dropped it (a dependency is not enabled)")
        problems += 1
    if any(k.startswith("BR2_PACKAGE_") for k in dropped):
        print("Hint: packages that duplicate BusyBox applets (bash, coreutils, findutils, gawk, grep, sed, ...) "
              "need BR2_PACKAGE_BUSYBOX_SHOW_OTHERS=y.")

    names = rootfs_names(a.rootfs)
    commands = []
    if a.required and Path(a.required).exists():
        commands = [c.strip() for c in Path(a.required).read_text(errors="replace").splitlines()
                    if c.strip() and not c.lstrip().startswith("#")]
    for c in commands:
        if not any(f"{d}/{c}" in names for d in ("bin", "sbin", "usr/bin", "usr/sbin")):
            print(f"::error::required command missing from the rootfs: {c}")
            problems += 1

    if problems:
        print(f"Baseline check FAILED with {problems} problem(s); not publishing.")
        sys.exit(1)
    print(f"Baseline check passed: all {len(wanted)} requested options kept, {len(commands)} required commands present.")


if __name__ == "__main__":
    main()
