#!/usr/bin/env python3
"""Work out a lab's level user pattern and level count.

  lab-settings.py <lab checkout> [--users PATTERN] [--levels N] [--github-output FILE]

The lab's own lab.json is the source of truth (user_pattern, level_count).
--users / --levels come from polyci.json and are only needed for labs without
a lab.json; if both are given and disagree, polyci.json wins with a warning.
"""
import argparse, json, sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lab")
    ap.add_argument("--users", default="")
    ap.add_argument("--levels", default="")
    ap.add_argument("--github-output")
    a = ap.parse_args()

    lab_json = Path(a.lab) / "lab.json"
    info = json.loads(lab_json.read_text()) if lab_json.exists() else {}
    users, levels = info.get("user_pattern", ""), info.get("level_count", "")
    source = "lab.json" if info else "polyci.json"

    for name, mine, theirs in (("users", a.users, users), ("levels", a.levels, levels)):
        if mine and theirs and str(mine) != str(theirs):
            print(f"::warning::polyci.json sets {name}={mine!r}, but the lab's lab.json says {theirs!r}; using polyci.json")
    users = a.users or users
    levels = a.levels or levels

    if not users or "{n}" not in users:
        sys.exit(f"::error::no usable user pattern (need something like 'level{{n}}'); "
                 f"add user_pattern to the lab's lab.json or 'users' to polyci.json")
    try:
        levels = int(levels)
        assert levels >= 1
    except (ValueError, AssertionError):
        sys.exit("::error::no usable level count; add level_count to the lab's lab.json or 'levels' to polyci.json")

    note = source if not info or not (a.users or a.levels) else "lab.json, with polyci.json overrides"
    print(f"users={users} levels={levels} (from {note})")
    if a.github_output:
        with open(a.github_output, "a") as fh:
            fh.write(f"users={users}\nlevels={levels}\n")


if __name__ == "__main__":
    main()
