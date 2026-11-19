#!/usr/bin/env python3
"""Check a PolyLab server archive (the per-student layer) before it is booted.

  check-scenario.py ARCHIVE [ARCHIVE ...]

The third layer of every image is the server's per-student archive. It may
only hold the folders etc/ and etc/polylab/ and regular files under
etc/polylab/ (scenario.json, scenario.env, ...): no other paths, links,
devices or setuid files. The page appends it as is, so CI checks every one it
makes the same way. (poly-server's own tests check this too.)
"""
import gzip, stat, sys


def entries(blob):
    data, off = gzip.decompress(blob), 0
    while off < len(data):
        h = data[off:off + 110]
        if h[:6] not in (b"070701", b"070702"):
            raise ValueError(f"not a newc cpio archive at byte {off}")
        f = lambda i: int(h[6 + 8 * i:14 + 8 * i], 16)
        mode, size, namesize = f(1), f(6), f(11)
        name = data[off + 110:off + 110 + namesize - 1].decode()
        off = (off + 110 + namesize + 3) & ~3
        off = (off + size + 3) & ~3
        if name == "TRAILER!!!":
            return
        yield name, mode


def check(path):
    problems = []
    try:
        items = list(entries(open(path, "rb").read()))
    except (OSError, ValueError, EOFError) as e:
        return [f"cannot read it: {e}"]
    for name, mode in items:
        clean = name.lstrip("./")
        if ".." in clean.split("/"):
            problems.append(f"{name}: '..' in the path")
        elif stat.S_ISDIR(mode):
            if clean not in ("etc", "etc/polylab") and not clean.startswith("etc/polylab/"):
                problems.append(f"{name}: a folder outside /etc/polylab/")
        elif stat.S_ISREG(mode):
            if not clean.startswith("etc/polylab/"):
                problems.append(f"{name}: a file outside /etc/polylab/")
            elif mode & (stat.S_ISUID | stat.S_ISGID | 0o022):
                problems.append(f"{name}: mode {stat.filemode(mode)} (setuid, setgid or writable by others)")
        else:
            problems.append(f"{name}: not a regular file or folder ({stat.filemode(mode)})")
    if not any(n.lstrip("./").startswith("etc/polylab/") for n, _ in items):
        problems.append("no files under /etc/polylab/")
    return problems


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bad = 0
    for path in sys.argv[1:]:
        problems = check(path)
        for p in problems:
            print(f"::error::{path}: {p}")
        bad += bool(problems)
        if not problems:
            print(f"{path}: ok (only /etc/polylab/)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
