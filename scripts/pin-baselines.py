#!/usr/bin/env python3
"""Pin baseline releases in polyci.json: set baselines.<type>.release.

  pin-baselines.py '{"networking": "baseline-networking-fc3d6448a7"}'

Edits only those "release" values, so the rest of the file keeps its
hand-written layout. Prints what changed; exits 0 even if nothing did.
"""
import json, re, sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "polyci.json"


def main():
    pins = json.loads(sys.argv[1])
    text = CONFIG.read_text()
    cfg = json.loads(text)
    want = json.loads(text)
    start = text.index('"baselines"')
    for btype, tag in sorted(pins.items()):
        if btype not in cfg["baselines"]:
            sys.exit(f"::error::{btype!r} is not a baseline in polyci.json")
        if cfg["baselines"][btype].get("release") == tag:
            print(f"{btype}: already pinned to {tag}")
            continue
        m = re.compile(r'"%s"\s*:\s*\{' % re.escape(btype)).search(text, start)
        r = re.compile(r'("release"\s*:\s*)"[^"]*"').search(text, m.end()) if m else None
        if not r:
            sys.exit(f"::error::could not find baselines.{btype}.release in polyci.json")
        text = text[:r.start()] + r.group(1) + json.dumps(tag) + text[r.end():]
        want["baselines"][btype]["release"] = tag
        print(f"{btype}: {cfg['baselines'][btype].get('release') or '(none)'} -> {tag}")
    if json.loads(text) != want:  # the edit hit the wrong "release": refuse
        sys.exit("::error::polyci.json edit went wrong; pin it by hand")
    CONFIG.write_text(text)


if __name__ == "__main__":
    main()
