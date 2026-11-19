#!/usr/bin/env bash
# Assemble one lab's v86 web page: web/index.html (the same page for every
# lab), web/vm-worker.js, the images and images/vms.json listing the VMs.
# Usage: assemble-site.sh <image-dir> <v86-bios-dir> <out-dir>
#   <image-dir>     holds vms.json plus the bzImage and initrd of each VM it
#                   lists (scripts/group.py), or, for a lab with one VM, just
#                   packaged.bzImage and packaged.rootfs.cpio.gz (vms.json is
#                   then written here)
#   <v86-bios-dir>  bios/ folder of a copy/v86 checkout (seabios.bin, vgabios.bin)
# Environment:
#   V86_VERSION     npm version of v86 (libv86.js + v86.wasm)
#   TITLE           page heading, e.g. "PolyBandit 3.1"
#   LABEL           footer text, e.g. the release tag
#   LEVELS          number of levels (shown on the landing page)
#   LAB_JSON        the lab's lab.json, if it has one; its form_url becomes a
#                   "Submit answers" button
#   SERVER          1 if the lab's values come from the PolyLab server (poly-server):
#                   the page asks for the email and appends the student's archive
#   IDENTITY        "polylab" if the lab's scripts read the student's identity
#                   from the kernel command line (the page asks for the email)
#   MEMORY_MB       memory of a one-VM lab's VM (default 256)
set -euo pipefail

IMAGES=${1:?image dir}
BIOS=${2:?v86 bios dir}
OUT=${3:?output dir}
: "${V86_VERSION:?set V86_VERSION}"
HERE=$(cd "$(dirname "$0")/.." && pwd)

mkdir -p "$OUT/v86" "$OUT/images"
cp "$HERE/web/index.html" "$HERE/web/vm-worker.js" "$OUT/"
if [ -f "$IMAGES/vms.json" ]; then
  cp "$IMAGES/vms.json" "$OUT/images/"
  for f in $(python3 -c 'import json,sys; [print(v["bzimage"], v["initrd"]) for v in json.load(open(sys.argv[1]))]' "$IMAGES/vms.json"); do
    cp "$IMAGES/$f" "$OUT/images/"
  done
else
  # One VM: the packaged image.
  printf '[{"name": "main", "bzimage": "packaged.bzImage", "initrd": "packaged.rootfs.cpio.gz", "memory_mb": %d, "visible": true}]\n' \
    "${MEMORY_MB:-256}" > "$OUT/images/vms.json"
  cp "$IMAGES/packaged.bzImage" "$IMAGES/packaged.rootfs.cpio.gz" "$OUT/images/"
fi

tmp=$(mktemp -d)
trap 'rm -r -- "$tmp"' EXIT
(cd "$tmp" && npm pack "v86@$V86_VERSION" --silent >/dev/null && tar xzf v86-*.tgz)
cp "$tmp/package/build/libv86.js" "$tmp/package/build/v86.wasm" "$OUT/v86/"
cp "$tmp/package/LICENSE" "$OUT/v86/LICENSE-v86.txt"
cp "$BIOS/seabios.bin" "$BIOS/vgabios.bin" "$OUT/v86/"
cp "$BIOS/COPYING.LESSER" "$OUT/v86/COPYING.LESSER-seabios.txt"

python3 - "$OUT/build.json" "${TITLE:-PolyLinux lab}" "${LABEL:-local build}" "$V86_VERSION" "${LAB_JSON:-}" "${LEVELS:-}" "${SERVER:-0}" "${IDENTITY:-}" <<'PY'
import json, os, sys
out, title, label, v86, lab_json, levels, server, identity = sys.argv[1:9]
info = {"title": title, "label": label, "v86": v86}
if server == "1":
    info["server"] = True
if identity:
    info["identity"] = identity
if levels:
    info["levels"] = int(levels)
if lab_json and os.path.exists(lab_json):
    url = json.load(open(lab_json)).get("form_url", "")
    if url.startswith("https://"):
        info["form_url"] = url
json.dump(info, open(out, "w"), indent=2)
PY
echo "Site assembled in $OUT ($(du -sh "$OUT" | cut -f1))"
