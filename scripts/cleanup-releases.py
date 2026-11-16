#!/usr/bin/env python3
"""Delete old PolyCI releases (and their tags) that nothing uses.

  cleanup-releases.py --repo OWNER/REPO [--keep-previous N] [--min-age-days D]
                      [--delete] [--summary FILE]

Only PolyCI's own tags are considered:
  baseline-<type>-<10 hex>                     baseline releases
  <lab>-<7 hex>-<10 hex>-release               lab releases
Any other tag is left alone.

Always kept:
  - baselines pinned in polyci.json (baselines.<type>.release)
  - the current release of every lab (the tag plan.py works out from
    polyci.json: the one the labs and GitHub Pages use)
Also kept:
  - the newest --keep-previous older releases of each baseline type and of
    each lab (default 1), to roll back to
  - anything younger than --min-age-days (default 7), e.g. a baseline
    pre-release that is still being tested
Deleted: everything else matching the patterns above, including releases of
baseline types and labs that are no longer in polyci.json, and tags left
behind without a release.

Without --delete nothing is changed: it only prints what it would delete.
"""
import argparse, datetime, json, os, re, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = re.compile(r"^baseline-(?P<name>.+)-[0-9a-f]{10}$")
LAB = re.compile(r"^(?P<name>.+)-[0-9a-f]{7}-[0-9a-f]{10}-release$")


def gh_lines(path, fields):
    """Every item of a paged GitHub API list, as dicts holding only these fields."""
    jq = ".[] | {" + ", ".join(fields) + "}"
    out = subprocess.run(["gh", "api", "--paginate", path, "--jq", jq],
                         check=True, capture_output=True, text=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def current_lab_releases():
    """Each lab's current release tag, exactly as the Build labs plan names it."""
    with tempfile.NamedTemporaryFile("r", suffix=".out") as fh:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "plan.py"), "--github-output", fh.name],
                       check=True, stdout=subprocess.DEVNULL)
        values = dict(line.rstrip("\n").split("=", 1) for line in open(fh.name) if "=" in line)
    tags = {}
    for key in ("matrix", "groups"):
        for entry in json.loads(values.get(key, '{"include": []}'))["include"]:
            if entry.get("release_tag"):
                tags[entry["lab"]] = entry["release_tag"]
    return tags


def classify(tag, baseline_types, lab_ids):
    """('baseline'|'lab', name) for PolyCI tags, else None. Longest known name wins."""
    for kind, pattern, known in (("baseline", BASELINE, baseline_types), ("lab", LAB, lab_ids)):
        m = pattern.match(tag)
        if m:
            names = [n for n in known if tag.startswith((f"baseline-{n}-" if kind == "baseline" else f"{n}-"))]
            return kind, (max(names, key=len) if names else m.group("name"))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--keep-previous", type=int, default=1)
    ap.add_argument("--min-age-days", type=float, default=7)
    ap.add_argument("--delete", action="store_true", help="really delete (default: only list)")
    ap.add_argument("--summary", help="append a Markdown report to this file (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--releases-file", help=argparse.SUPPRESS)  # testing: JSON instead of the API
    ap.add_argument("--tags-file", help=argparse.SUPPRESS)
    ap.add_argument("--now", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.keep_previous < 0 or a.min_age_days < 0:
        sys.exit("--keep-previous and --min-age-days must not be negative")

    cfg = json.loads((ROOT / "polyci.json").read_text())
    pinned = {b["release"] for b in cfg["baselines"].values() if b.get("release")}
    current = current_lab_releases()
    releases = json.loads(Path(a.releases_file).read_text()) if a.releases_file else gh_lines(f"repos/{a.repo}/releases", ["tag_name", "created_at", "draft"])
    tag_names = ([t["name"] for t in json.loads(Path(a.tags_file).read_text())] if a.tags_file
                 else [t["name"] for t in gh_lines(f"repos/{a.repo}/tags", ["name"])])
    now = datetime.datetime.fromisoformat(a.now) if a.now else datetime.datetime.now(datetime.timezone.utc)

    rows = []    # (tag, kind, name, age_days or None, has_release, decision, reason)
    groups = {}  # (kind, name) -> [(created, tag)] for releases not kept for another reason
    for r in releases:
        if r.get("draft"):
            continue
        tag = r["tag_name"]
        c = classify(tag, cfg["baselines"], cfg["labs"])
        if not c:
            continue
        created = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        age = (now - created).total_seconds() / 86400
        kind, name = c
        known = name in (cfg["baselines"] if kind == "baseline" else cfg["labs"])
        if tag in pinned:
            rows.append([tag, kind, name, age, True, "keep", "pinned in polyci.json"])
        elif tag in current.values():
            rows.append([tag, kind, name, age, True, "keep", "current lab release"])
        elif age < a.min_age_days:
            rows.append([tag, kind, name, age, True, "keep", f"younger than {a.min_age_days:g} days"])
        elif not known:
            rows.append([tag, kind, name, age, True, "delete", f"{kind} {name!r} is no longer in polyci.json"])
        else:
            groups.setdefault((kind, name), []).append((created, tag, age))
    for (kind, name), items in groups.items():
        for i, (_, tag, age) in enumerate(sorted(items, reverse=True)):
            if i < a.keep_previous:
                rows.append([tag, kind, name, age, True, "keep", f"previous release kept for rollback ({i + 1} of {a.keep_previous})"])
            else:
                rows.append([tag, kind, name, age, True, "delete", "older than the kept releases"])

    with_release = {r["tag_name"] for r in releases}
    for tag in tag_names:
        c = classify(tag, cfg["baselines"], cfg["labs"])
        if c and tag not in with_release:
            keep = tag in pinned or tag in current.values()
            rows.append([tag, c[0], c[1], None, False, "keep" if keep else "delete",
                         "in use (but its release is missing!)" if keep else "tag without a release"])

    rows.sort(key=lambda r: (r[5] != "delete", r[1], r[2], r[0]))
    deletes = [r for r in rows if r[5] == "delete"]
    mode = "Deleting" if a.delete else "Would delete (dry run)"
    lines = [f"## Release cleanup: {len(deletes)} to delete, {len(rows) - len(deletes)} kept", "",
             f"keep_previous={a.keep_previous}, min_age_days={a.min_age_days:g}"
             + ("" if a.delete else ". **Dry run: nothing was deleted.**"), "",
             "| | Tag | Age | Why |", "|---|---|---|---|"]
    for tag, kind, name, age, has_rel, decision, reason in rows:
        mark = "🗑️ delete" if decision == "delete" else "keep"
        age_s = f"{age:.0f} d" if age is not None else "-"
        lines.append(f"| {mark} | `{tag}`{'' if has_rel else ' (tag only)'} | {age_s} | {reason} |")
    report = "\n".join(lines) + "\n"
    print(report)
    if a.summary:
        with open(a.summary, "a") as fh:
            fh.write(report)

    missing = [r[0] for r in rows if r[6].startswith("in use (but")]
    for tag in missing:
        print(f"::warning::{tag} is in use but has no release")
    if not a.delete:
        return
    failed = 0
    for tag, kind, name, age, has_rel, decision, reason in deletes:
        cmd = (["gh", "release", "delete", tag, "-R", a.repo, "--cleanup-tag", "--yes"] if has_rel
               else ["gh", "api", "-X", "DELETE", f"repos/{a.repo}/git/refs/tags/{tag}"])
        print(f"{mode}: {tag}", flush=True)
        if subprocess.run(cmd).returncode != 0:
            print(f"::error::could not delete {tag}")
            failed += 1
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
