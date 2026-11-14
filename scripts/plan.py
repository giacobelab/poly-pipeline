#!/usr/bin/env python3
"""Turn polyci.json into the lab build matrix.

  plan.py [--labs a,b] [--baseline-override TYPE=TAG] [--github-output FILE]

Prints the matrix as JSON and, with --github-output, writes `matrix=<json>`
and `count=<n>` for GitHub Actions. Each entry carries everything one lab's
build job needs, including the baseline release it packages on. A lab whose
baseline type has no release pinned yet is still listed, with
baseline_tag="", and its job reports "skipped: no baseline" instead of failing.
"""
import argparse, json, re, sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "polyci.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^baseline-[a-z0-9-]+-[0-9a-f]{10}$")


def die(msg):
    print(f"::error::polyci.json: {msg}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labs", default="", help="comma-separated lab ids (default: all)")
    ap.add_argument("--baseline-override", default="",
                    help="TYPE=TAG: use this baseline release for TYPE (e.g. to test a pre-release)")
    ap.add_argument("--github-output")
    a = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    baselines, labs = cfg["baselines"], cfg["labs"]

    override = {}
    if a.baseline_override.strip():
        btype, _, tag = a.baseline_override.strip().partition("=")
        if btype not in baselines or not TAG.match(tag) or not tag.startswith(f"baseline-{btype}-"):
            die(f"bad --baseline-override {a.baseline_override!r} (want TYPE=baseline-TYPE-<hash>)")
        override[btype] = tag

    for name, b in baselines.items():
        if b.get("release") and not (TAG.match(b["release"]) and b["release"].startswith(f"baseline-{name}-")):
            die(f"baselines.{name}.release must look like baseline-{name}-<10 hex>, got {b['release']!r}")

    wanted = [x.strip() for x in a.labs.split(",") if x.strip()] or list(labs)
    unknown = [x for x in wanted if x not in labs]
    if unknown:
        die(f"unknown lab(s): {unknown}; known: {list(labs)}")

    matrix = []
    for lab_id in wanted:
        lab = labs[lab_id]
        btype = lab["baseline"]
        if btype not in baselines:
            die(f"labs.{lab_id}.baseline {btype!r} is not a defined baseline")
        if baselines[btype].get("enabled", True) is False:
            die(f"labs.{lab_id} uses baseline {btype!r}, which is disabled")
        if not SHA.match(lab["ref"]):
            die(f"labs.{lab_id}.ref must be a full 40-character commit SHA")
        if lab.get("boot_test") not in ("v3", "v31", "generic"):
            die(f"labs.{lab_id}.boot_test must be v3, v31 or generic")
        renames = " ".join(f"--rename {k}={v}" for k, v in sorted(lab.get("rename", {}).items()))
        matrix.append({
            "lab": lab_id,
            "title": lab.get("title", lab_id),
            "repo": lab["repo"],
            "ref": lab["ref"],
            "subdir": lab.get("subdir", "."),
            "renames": renames,
            "baseline_type": btype,
            "baseline_tag": override.get(btype, baselines[btype].get("release", "")),
            "baseline_is_override": btype in override,
            "boot_test": lab["boot_test"],
            "self_test": lab.get("self_test", ""),
            "users": lab.get("users", "level{n}"),
            "levels": int(lab.get("levels", 10)),
        })

    out = json.dumps({"include": matrix})
    print(json.dumps({"include": matrix}, indent=2))
    if a.github_output:
        with open(a.github_output, "a") as fh:
            fh.write(f"matrix={out}\ncount={len(matrix)}\n")


if __name__ == "__main__":
    main()
