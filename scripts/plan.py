#!/usr/bin/env python3
"""Turn polyci.json into the lab build matrix.

  plan.py [--labs a,b] [--baseline-override TYPE=TAG,...] [--check-releases REPO]
          [--github-output FILE]

Each matrix entry carries everything one lab's job needs, plus a mode:

  build   build lab.cpio.gz, join, run every test (and publish on main)
  reuse   the release for this exact lab commit + baseline already exists and
          was made by the same pipeline (same fingerprint): the job only
          downloads the released images and rebuilds the web page
  skip    no baseline release is pinned for the lab's baseline type yet

The fingerprint covers everything in PolyCI that shapes a lab image, its tests
or its page: scripts/, web/, tools/Dockerfile, the labs workflow, the v86
settings and the lab's own polyci.json entry. Without --check-releases (the
weekly run, or a forced run) every lab with a baseline is built.

With --github-output, writes matrix=<json>, count=<n> and any_build=<bool>.
"""
import argparse, hashlib, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "polyci.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^baseline-[a-z0-9-]+-[0-9a-f]{10}$")
PIPELINE = ["scripts", "web", "tools/Dockerfile", ".github/workflows/labs.yml"]


def die(msg):
    print(f"::error::polyci.json: {msg}")
    sys.exit(1)


def pipeline_digest():
    h = hashlib.sha256()
    for entry in PIPELINE:
        p = ROOT / entry
        files = sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts) if p.is_dir() else [p]
        for f in files:
            h.update(str(f.relative_to(ROOT)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def released_fingerprint(repo, tag):
    """Fingerprint recorded in an existing lab release, or None if there is no such release."""
    try:
        out = subprocess.run(["gh", "release", "download", tag, "-R", repo, "-p", "release.json", "-O", "-"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout).get("fingerprint", "")
    except json.JSONDecodeError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labs", default="", help="comma-separated lab ids (default: all)")
    ap.add_argument("--baseline-override", default="",
                    help="TYPE=TAG or just TAG, comma-separated: use these baseline releases "
                         "(e.g. to test a pre-release)")
    ap.add_argument("--check-releases", default="", metavar="OWNER/REPO",
                    help="reuse lab releases in this repo when their fingerprint matches")
    ap.add_argument("--github-output")
    a = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    baselines, labs = cfg["baselines"], cfg["labs"]

    override = {}
    for item in (x.strip() for x in a.baseline_override.split(",")):
        if not item:
            continue
        btype, eq, tag = item.partition("=")
        if not eq:  # a bare tag: the type is in its name
            tag = item
            btype = max((n for n in baselines if tag.startswith(f"baseline-{n}-")), key=len, default="")
        btype, tag = btype.strip(), tag.strip()
        if btype not in baselines or not TAG.match(tag) or not tag.startswith(f"baseline-{btype}-"):
            die(f"bad --baseline-override {item!r} (want baseline-TYPE-<hash> or TYPE=baseline-TYPE-<hash>; "
                f"types: {list(baselines)})")
        if btype in override and override[btype] != tag:
            die(f"two overrides for baseline {btype!r}")
        override[btype] = tag

    for name, b in baselines.items():
        if b.get("release") and not (TAG.match(b["release"]) and b["release"].startswith(f"baseline-{name}-")):
            die(f"baselines.{name}.release must look like baseline-{name}-<10 hex>, got {b['release']!r}")

    wanted = [x.strip() for x in a.labs.split(",") if x.strip()] or list(labs)
    unknown = [x for x in wanted if x not in labs]
    if unknown:
        die(f"unknown lab(s): {unknown}; known: {list(labs)}")

    pipeline = pipeline_digest()
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
        if lab.get("boot_test") not in ("v31", "generic"):
            die(f"labs.{lab_id}.boot_test must be v31 or generic")
        if "levels" in lab and (not isinstance(lab["levels"], int) or lab["levels"] < 1):
            die(f"labs.{lab_id}.levels must be a positive whole number")

        baseline_tag = override.get(btype, baselines[btype].get("release", ""))
        release_tag = f"{lab_id}-{lab['ref'][:7]}-{baseline_tag.rsplit('-', 1)[-1]}-release" if baseline_tag else ""
        fingerprint = hashlib.sha256(json.dumps(
            {"pipeline": pipeline, "v86": cfg["v86"], "lab": lab, "baseline": baseline_tag},
            sort_keys=True).encode()).hexdigest()

        if not baseline_tag:
            mode, why = "skip", f"no '{btype}' baseline release is pinned yet"
        elif a.check_releases and btype not in override:
            found = released_fingerprint(a.check_releases, release_tag)
            if found == fingerprint:
                mode, why = "reuse", f"{release_tag} already released by this pipeline"
            elif found is None:
                mode, why = "build", f"{release_tag} not released yet"
            else:
                mode, why = "build", f"{release_tag} exists, but PolyCI's pipeline has changed since"
        else:
            mode, why = "build", "full build requested (weekly run, forced run or baseline test)"

        matrix.append({
            "lab": lab_id,
            "title": lab.get("title", lab_id),
            "repo": lab["repo"],
            "ref": lab["ref"],
            "subdir": lab.get("subdir", "."),
            "renames": " ".join(f"--rename {k}={v}" for k, v in sorted(lab.get("rename", {}).items())),
            "baseline_type": btype,
            "baseline_tag": baseline_tag,
            "baseline_is_override": btype in override,
            "release_tag": release_tag,
            "fingerprint": fingerprint,
            "mode": mode,
            "why": why,
            "boot_test": lab["boot_test"],
            "self_test": lab.get("self_test", ""),
            # Optional overrides; normally read from the lab's own lab.json.
            "users": lab.get("users", ""),
            "levels": str(lab.get("levels", "")),
        })

    tested = {m["baseline_type"] for m in matrix if m["mode"] == "build"}
    untested = sorted(set(override) - tested)
    if untested:
        die(f"no lab in this run uses the overridden baseline(s) {untested}; "
            f"add a lab that does to 'labs' (or leave 'labs' blank)")

    for m in matrix:
        print(f"{m['lab']:<24} {m['mode']:<6} {m['why']}")
    out = json.dumps({"include": matrix})
    if a.github_output:
        with open(a.github_output, "a") as fh:
            fh.write(f"matrix={out}\ncount={len(matrix)}\noverrides={json.dumps(override, sort_keys=True)}\n"
                     f"any_build={'true' if any(m['mode'] == 'build' for m in matrix) else 'false'}\n")


if __name__ == "__main__":
    main()
