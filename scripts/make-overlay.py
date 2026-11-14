#!/usr/bin/env python3
"""Build a lab overlay: a gzipped newc cpio archive holding one folder of a
lab repo, at one pinned commit, placed under /root.

The overlay is meant to be *appended* to a baseline rootfs.cpio.gz:

    cat baseline/rootfs.cpio.gz lab.cpio.gz > packaged.rootfs.cpio.gz

The Linux kernel unpacks concatenated initramfs archives in order, so the
overlay's files land on top of the base and the base's bytes stay unchanged
(its checksum still identifies exactly which tested base is inside).

Matches giacobe/buildroot-builder2's package_rootfs_payload.py so a joined
image behaves like one made by 03-package-payload.sh:
  - directories 0755 (including /root), owner root:root
  - files 0755 if *.sh or a PolyLinux helper name, else 0644
  - CRLF -> LF in text files (files without NUL bytes)
Differences, on purpose:
  - files come from the git commit, not a working tree, so a Windows checkout
    cannot change modes or line endings
  - every timestamp is the commit time and entries are sorted, so the same
    commit always gives a byte-identical lab.cpio.gz

Usage:
  make-overlay.py --repo DIR --ref SHA --subdir PATH --out lab.cpio.gz
                  [--rename FROM=TO ...] [--manifest files.txt]
"""
import argparse, gzip, stat, subprocess, sys
from pathlib import PurePosixPath

HELPER_NAMES = {"nextlevel", "prevlevel", "process-helper", "profile", "startlevel", "validate"}
ARCHIVE_DEST = "root"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True).stdout


def file_mode(name):
    p = PurePosixPath(name)
    executable = p.suffix == ".sh" or p.name in HELPER_NAMES
    return stat.S_IFREG | (0o755 if executable else 0o644)


def normalize_text(data):
    return data if b"\x00" in data else data.replace(b"\r\n", b"\n")


def newc_entry(name, mode, data, ino, mtime, nlink):
    namez = name.encode() + b"\0"
    fields = [ino, mode, 0, 0, nlink, mtime, len(data), 0, 0, 0, 0, len(namez), 0]
    head = b"070701" + b"".join(b"%08X" % f for f in fields)
    out = head + namez
    out += b"\0" * ((4 - len(out) % 4) % 4)
    out += data
    out += b"\0" * ((4 - len(data) % 4) % 4)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="git checkout of the lab repo")
    ap.add_argument("--ref", required=True, help="commit to package")
    ap.add_argument("--subdir", default=".", help="folder of the repo that becomes /root")
    ap.add_argument("--rename", action="append", default=[], metavar="FROM=TO",
                    help="rename a file inside the folder, e.g. installbandit.sh=install.sh")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", help="write 'mode path sha256' lines here")
    a = ap.parse_args()

    subdir = PurePosixPath(a.subdir.strip("/") or ".")
    renames = dict(r.split("=", 1) for r in a.rename)
    mtime = int(git(a.repo, "show", "-s", "--format=%ct", a.ref).strip())

    # Tracked files only, straight from the commit.
    spec = [] if str(subdir) == "." else ["--", str(subdir)]
    listing = git(a.repo, "ls-tree", "-r", "-z", "--full-tree", a.ref, *spec).split(b"\0")
    files = {}
    for line in filter(None, listing):
        meta, path = line.split(b"\t", 1)
        gmode, gtype, oid = meta.split()
        path = path.decode()
        if gtype != b"blob":
            continue  # submodules are not packaged
        rel = str(PurePosixPath(path).relative_to(subdir)) if str(subdir) != "." else path
        if rel.split("/")[0] in (".git", ".github"):
            continue
        rel = renames.get(rel, rel)
        data = git(a.repo, "cat-file", "blob", oid.decode())
        if gmode == b"120000":
            files[rel] = (stat.S_IFLNK | 0o777, data)
        else:
            files[rel] = (file_mode(rel), normalize_text(data))
    if not files:
        sys.exit(f"no files under {subdir} at {a.ref}")
    missing = [f for f in renames.values() if f not in files]
    if missing:
        sys.exit(f"rename target(s) not produced: {missing}")

    dirs = {ARCHIVE_DEST}
    for rel in files:
        parent = PurePosixPath(ARCHIVE_DEST, rel).parent
        while str(parent) not in (".", ""):
            dirs.add(str(parent))
            parent = parent.parent

    blob = bytearray()
    ino = 0x50000  # well above a Buildroot base's inode numbers
    for d in sorted(dirs):
        ino += 1
        blob += newc_entry(d, stat.S_IFDIR | 0o755, b"", ino, mtime, 2)
    for rel in sorted(files):
        mode, data = files[rel]
        ino += 1
        blob += newc_entry(f"{ARCHIVE_DEST}/{rel}", mode, data, ino, mtime, 1)
    blob += newc_entry("TRAILER!!!", 0, b"", 0, 0, 1)

    with open(a.out, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(bytes(blob))

    if a.manifest:
        import hashlib
        with open(a.manifest, "w") as m:
            for rel in sorted(files):
                mode, data = files[rel]
                m.write(f"{stat.filemode(mode)} /{ARCHIVE_DEST}/{rel} {hashlib.sha256(data).hexdigest()}\n")
    print(f"{a.out}: {len(files)} files under /{ARCHIVE_DEST} from {a.subdir}@{a.ref[:12]}")


if __name__ == "__main__":
    main()
