#!/usr/bin/env python3
"""Move each lab's pinned ref in polyci.json to the newest commit on its
repo's default branch, and describe what changed.

  bump-labs.py [--body FILE] [--github-output FILE]

Only labs.<id>.ref and labs.<id>.vms.<vm>.ref are changed (a lab described by
its lab.toml has both: the lab's own repo, and any VMs from other repos). The builder and from_lab baseline configs are
checked too, but only reported: moving them means building and promoting a
new baseline, which should be a deliberate step.
Writes changed=true|false (and the PR body) for GitHub Actions.
"""
import argparse, json, subprocess
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "polyci.json"


def head(repo):
    out = subprocess.run(["git", "ls-remote", f"https://github.com/{repo}", "HEAD"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.split()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--body", default="bump-body.md")
    ap.add_argument("--github-output")
    a = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    bumped, notes, problems = [], [], []

    for lab_id, lab in cfg["labs"].items():
        # The lab's own repo (if it has one), and each VM's repo (if pinned): each moves on its own.
        targets = [(lab_id, lab)] if "repo" in lab else []
        targets += [(f"{lab_id}/{n}", v) for n, v in lab.get("vms", {}).items() if "repo" in v]
        for label, entry in targets:
            new = head(entry["repo"])
            if new is None:
                problems.append(f"- `{label}`: could not read {entry['repo']}")
            elif new != entry["ref"]:
                bumped.append(f"- **{lab.get('title', lab_id)}** (`{label}`): "
                              f"[`{entry['ref'][:7]}…{new[:7]}`](https://github.com/{entry['repo']}/compare/{entry['ref']}...{new})")
                entry["ref"] = new

    for what, repo, ref in [("builder", cfg["builder"]["repo"], cfg["builder"]["ref"])] + [
            (f"baseline `{name}` config", b["from_lab"]["repo"], b["from_lab"]["ref"])
            for name, b in cfg["baselines"].items() if b.get("from_lab")]:
        new = head(repo)
        if new and new != ref:
            notes.append(f"- {what}: {repo} has [newer commits](https://github.com/{repo}/compare/{ref}...{new}). "
                         "Not changed here: moving it needs a new baseline (Publish baseline → test → promote → pin).")

    if bumped:
        CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    body = ["Weekly check of the lab repos pinned in `polyci.json`.", ""]
    body += ["### Labs moved to their newest commit", *bumped, ""] if bumped else ["No lab has new commits.", ""]
    body += ["### Also newer upstream (not changed)", *notes, ""] if notes else []
    body += ["### Problems", *problems, ""] if problems else []
    body += ["The full lab pipeline runs on this PR. Merge it when **all labs pass** is green; "
             "that publishes the new lab releases and updates Pages."]
    Path(a.body).write_text("\n".join(body) + "\n")
    print("\n".join(body))
    if a.github_output:
        with open(a.github_output, "a") as fh:
            fh.write(f"changed={'true' if bumped else 'false'}\n")


if __name__ == "__main__":
    main()
