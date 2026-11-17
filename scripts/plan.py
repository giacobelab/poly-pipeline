#!/usr/bin/env python3
"""Turn polyci.json into the lab build matrix.

  plan.py [--labs a,b] [--baseline-override ITEM,...] [--check-releases REPO]
          [--repo OWNER/REPO]

--baseline-override items (comma-separated):
  baseline-TYPE-<hash> or TYPE=baseline-TYPE-<hash>   that release
  TYPE                                                the newest release of TYPE
  latest                                              the newest release of every
                                                      type that is newer than its pin
TYPE and latest look the releases up in --repo (default $GITHUB_REPOSITORY).
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

A lab with "vms" (several VMs on one page, e.g. netsec-snort) goes into a
second matrix, groups, which the workflow's multi-VM job runs. Its release tag
is <lab>-<7 hex of the VM commits>-<10 hex of the baseline tags>-release.

With --github-output, writes matrix=<json>, count=<n>, groups=<json>,
group_count=<n>, any_build=<bool> and overrides=<json>.
"""
import argparse, hashlib, json, os, re, subprocess, sys
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


def baseline_releases(repo, baselines):
    """{type: [(created_at, tag), ...] newest first} for this repo's baseline releases."""
    if not repo:
        die("naming a baseline by type or 'latest' needs --repo (or GITHUB_REPOSITORY)")
    try:
        out = subprocess.run(["gh", "api", "--paginate", f"repos/{repo}/releases", "--jq",
                              '.[] | select(.draft | not) | [.created_at, .tag_name] | @tsv'],
                             check=True, capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as e:
        die(f"could not list the releases of {repo}: {e}")
    found = {}
    for line in out.splitlines():
        created, _, tag = line.partition("\t")
        btype = max((n for n in baselines if tag.startswith(f"baseline-{n}-")), key=len, default="")
        if btype and TAG.match(tag):
            found.setdefault(btype, []).append((created, tag))
    return {t: sorted(v, reverse=True) for t, v in found.items()}


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


NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


def plan_group(lab_id, lab, cfg, baselines, override, pipeline, decide):
    """Matrix entry for a lab made of several VMs on one page."""
    vms = lab["vms"]
    if not isinstance(vms, dict) or not vms:
        die(f"labs.{lab_id}.vms must be an object with at least one VM")
    if lab.get("boot_test") != "netsec-snort":
        die(f"labs.{lab_id}.boot_test must be netsec-snort (the only multi-VM test so far)")
    if lab.get("identity", "polylab") != "polylab":
        die(f"labs.{lab_id}.identity must be polylab")
    ov = lab.get("overlay", {})
    visible = [n for n, v in vms.items() if v.get("visible")]
    if len(visible) != 1:
        die(f"labs.{lab_id}: exactly one VM must have \"visible\": true (has {visible})")
    out_vms, tags = [], {}
    for name, v in vms.items():
        if not NAME.match(name):
            die(f"labs.{lab_id}.vms: bad VM name {name!r} (lowercase letters, digits, -)")
        btype = v.get("baseline", "")
        if btype not in baselines:
            die(f"labs.{lab_id}.vms.{name}.baseline {btype!r} is not a defined baseline")
        if baselines[btype].get("enabled", True) is False:
            die(f"labs.{lab_id}.vms.{name} uses baseline {btype!r}, which is disabled")
        if not SHA.match(v.get("ref", "")):
            die(f"labs.{lab_id}.vms.{name}.ref must be a full 40-character commit SHA")
        mem = v.get("memory_mb", 256)
        if not isinstance(mem, int) or not 128 <= mem <= 1024:
            die(f"labs.{lab_id}.vms.{name}.memory_mb must be a whole number from 128 to 1024")
        tag = override.get(btype, baselines[btype].get("release", ""))
        tags[btype] = tag
        out_vms.append({"name": name, "repo": v["repo"], "ref": v["ref"], "baseline_type": btype,
                        "baseline_tag": tag, "baseline_is_override": btype in override,
                        "visible": bool(v.get("visible")), "memory_mb": mem})
    unpinned = sorted(t for t, tag in tags.items() if not tag)
    refs_hash = hashlib.sha256("".join(f"{v['name']}={v['ref']}\n" for v in out_vms).encode()).hexdigest()[:7]
    base_hash = hashlib.sha256("".join(f"{t}={tags[t]}\n" for t in sorted(tags)).encode()).hexdigest()[:10]
    release_tag = "" if unpinned else f"{lab_id}-{refs_hash}-{base_hash}-release"
    fingerprint = hashlib.sha256(json.dumps(
        {"pipeline": pipeline, "v86": cfg["v86"], "lab": lab, "baselines": tags},
        sort_keys=True).encode()).hexdigest()
    if unpinned:
        mode, why = "skip", f"no baseline release is pinned yet for {unpinned}"
    else:
        mode, why = decide(release_tag, fingerprint, list(tags))
    return {
        "lab": lab_id,
        "title": lab.get("title", lab_id),
        "vms": out_vms,
        "baseline_tags": sorted(set(tags.values()) - {""}),
        "overlay_subdir": ov.get("subdir", "rootfs-overlay"),
        "overlay_dest": ov.get("dest", "/"),
        "overlay_permissions": ov.get("permissions", ""),
        "overlay_checksums": ov.get("checksums", ""),
        "boot_test": lab["boot_test"],
        "release_tag": release_tag,
        "fingerprint": fingerprint,
        "mode": mode,
        "why": why,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labs", default="", help="comma-separated lab ids (default: all)")
    ap.add_argument("--baseline-override", default="",
                    help="comma-separated: TAG, TYPE=TAG, TYPE (its newest release) or latest "
                         "(every type's newest release that is newer than its pin)")
    ap.add_argument("--check-releases", default="", metavar="OWNER/REPO",
                    help="reuse lab releases in this repo when their fingerprint matches")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                    help="where to look up baseline releases for TYPE and latest")
    ap.add_argument("--github-output")
    a = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    baselines, labs = cfg["baselines"], cfg["labs"]

    override = {}
    items = [x.strip() for x in a.baseline_override.split(",") if x.strip()]
    newest = None
    if any(i == "latest" or i in baselines for i in items):
        newest = baseline_releases(a.repo, baselines)
    expanded = []
    for item in items:
        if item == "latest":
            newer = [newest[t][0][1] for t in baselines
                     if newest.get(t) and newest[t][0][1] != baselines[t].get("release")]
            if not newer:
                die("latest: every baseline type's newest release is already pinned; nothing to test")
            expanded += newer
        elif item in baselines:
            if not newest.get(item):
                die(f"{item}: no baseline-{item}-... release found in {a.repo}")
            expanded.append(newest[item][0][1])
        else:
            expanded.append(item)
    for item in expanded:
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
    matrix, groups = [], []

    def decide(release_tag, fingerprint, types):
        if a.check_releases and not (set(types) & set(override)):
            found = released_fingerprint(a.check_releases, release_tag)
            if found == fingerprint:
                return "reuse", f"{release_tag} already released by this pipeline"
            if found is None:
                return "build", f"{release_tag} not released yet"
            return "build", f"{release_tag} exists, but PolyCI's pipeline has changed since"
        return "build", "full build requested (weekly run, forced run or baseline test)"

    for lab_id in wanted:
        lab = labs[lab_id]
        if "vms" in lab:
            groups.append(plan_group(lab_id, lab, cfg, baselines, override, pipeline, decide))
            continue
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
        else:
            mode, why = decide(release_tag, fingerprint, [btype])

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
    tested |= {v["baseline_type"] for g in groups if g["mode"] == "build" for v in g["vms"]}
    untested = sorted(set(override) - tested)
    if untested:
        die(f"no lab in this run uses the overridden baseline(s) {untested}; "
            f"add a lab that does to 'labs' (or leave 'labs' blank)")

    if override:
        print("Baselines under test (pinned -> this run):")
        for t in baselines:
            if t in override:
                print(f"  {t:<16} {baselines[t].get('release') or '(none)'} -> {override[t]}")
        print()
    for m in matrix + groups:
        print(f"{m['lab']:<24} {m['mode']:<6} {m['why']}")
    out = json.dumps({"include": matrix})
    gout = json.dumps({"include": groups})
    any_build = any(m["mode"] == "build" for m in matrix + groups)
    if a.github_output:
        with open(a.github_output, "a") as fh:
            fh.write(f"matrix={out}\ncount={len(matrix)}\ngroups={gout}\ngroup_count={len(groups)}\n"
                     f"overrides={json.dumps(override, sort_keys=True)}\n"
                     f"any_build={'true' if any_build else 'false'}\n")


if __name__ == "__main__":
    main()
