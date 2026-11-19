#!/usr/bin/env python3
"""Read a lab's lab.toml: what the lab is and how to build and test it.

A lab whose polyci.json entry has only "repo" and "ref" (plus, for several
VMs, "vms" with only "repo" and "ref" each) describes itself in a lab.toml at
the root of that repo, at that commit. This module fetches it, checks it, and
turns it into the same entry polyci.json used to hold, so the rest of the
pipeline works the same either way. docs/lab-toml.md describes the format.

  manifest.py check FILE [--id LAB]      check a lab.toml (for lab authors)
  manifest.py show  FILE [--id LAB]      print the polyci.json entry it becomes
"""
import argparse, base64, json, re, subprocess, sys, tomllib, urllib.error, urllib.request

SCHEMA = 1
NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
LAB_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,40}$")
BOOT_TESTS = ("generic", "v31", "netsec-snort")


class ManifestError(Exception):
    pass


def fetch(repo, ref, path="lab.toml"):
    """The text of path in repo at ref, or None if the repo has no such file."""
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise ManifestError(f"could not fetch {path} from {repo}@{ref[:12]}: HTTP {e.code}")
    except (urllib.error.URLError, OSError):
        pass
    # A private repo (or no direct network): ask the API with the gh login.
    try:
        out = subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}?ref={ref}", "--jq", ".content"],
                             capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    if out.returncode != 0:
        if "404" in out.stderr or "Not Found" in out.stderr:
            return None
        raise ManifestError(f"could not fetch {path} from {repo}@{ref[:12]}: {out.stderr.strip()}")
    return base64.b64decode(out.stdout).decode()


def _keys(where, table, allowed, required=()):
    if not isinstance(table, dict):
        raise ManifestError(f"{where} must be a table")
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise ManifestError(f"{where}: unknown key(s) {unknown} (allowed: {sorted(allowed)})")
    missing = [k for k in required if k not in table]
    if missing:
        raise ManifestError(f"{where}: missing {missing}")


def _type(where, value, kind, what):
    ok = isinstance(value, kind) and not (kind is int and isinstance(value, bool))
    if not ok:
        raise ManifestError(f"{where} must be {what}, got {value!r}")
    return value


def _path_list(where, value):
    _type(where, value, list, "a list of paths")
    out = []
    for p in value:
        _type(where, p, str, "a list of paths")
        if not p.startswith("/") or ".." in p.split("/") or p.endswith("/"):
            raise ManifestError(f"{where}: {p!r} must be an absolute file path, like /etc/inittab")
        out.append(p)
    return out


def parse(text, lab_id):
    """lab.toml text -> polyci.json-style entry (without repo/ref)."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"lab.toml is not valid TOML: {e}")
    _keys("lab.toml", doc, {"schema", "id", "title", "baseline", "overlay", "levels", "test", "server", "vm"},
          required=("schema", "id", "title"))
    if doc["schema"] != SCHEMA:
        raise ManifestError(f"lab.toml schema {doc['schema']!r}: this pipeline reads schema {SCHEMA}")
    _type("id", doc["id"], str, "a string")
    if doc["id"] != lab_id:
        raise ManifestError(f"lab.toml says id = {doc['id']!r}, but polyci.json lists it as {lab_id!r}")
    _type("title", doc["title"], str, "a string")
    entry = {"title": doc["title"]}

    ov = doc.get("overlay", {})
    _keys("[overlay]", ov, {"folder", "dest", "permissions", "checksums", "rename", "replaces"})
    for k in ("folder", "dest", "permissions", "checksums"):
        if k in ov:
            _type(f"overlay.{k}", ov[k], str, "a string")
    if "dest" in ov and not ov["dest"].startswith("/"):
        raise ManifestError("overlay.dest must be an absolute path, like /root or /")
    rename = ov.get("rename", {})
    _type("overlay.rename", rename, dict, "a table of FROM = \"TO\"")
    for k, v in rename.items():
        _type(f"overlay.rename.{k}", v, str, "a string")
    replaces = _path_list("overlay.replaces", ov.get("replaces", []))

    test = doc.get("test", {})
    _keys("[test]", test, {"boot", "self_test"}, required=("boot",))
    if test["boot"] not in BOOT_TESTS:
        raise ManifestError(f"test.boot must be one of {list(BOOT_TESTS)}, got {test['boot']!r}")
    entry["boot_test"] = test["boot"]

    server = doc.get("server", {})
    _keys("[server]", server, {"values"})
    if "values" in server:
        _type("server.values", server["values"], bool, "true or false")

    levels = doc.get("levels", {})
    _keys("[levels]", levels, {"count", "users"})
    if "count" in levels and _type("levels.count", levels["count"], int, "a whole number") < 1:
        raise ManifestError("levels.count must be at least 1")
    if "users" in levels and "{n}" not in _type("levels.users", levels["users"], str, "a string"):
        raise ManifestError("levels.users must contain {n}, like \"level{n}\"")

    vms = doc.get("vm")
    if vms is None:
        # One VM: the usual lab, built into /root of one image.
        if "baseline" not in doc:
            raise ManifestError("lab.toml needs baseline = \"<type>\" (or [[vm]] tables for several VMs)")
        entry["baseline"] = _type("baseline", doc["baseline"], str, "a string")
        entry["subdir"] = ov.get("folder", ".")
        for k in ("dest", "permissions", "checksums"):
            if k in ov:
                entry[f"overlay_{k}"] = ov[k]
        if rename:
            entry["rename"] = dict(rename)
        if replaces:
            entry["overlay_replaces"] = replaces
        if "self_test" in test:
            entry["self_test"] = _type("test.self_test", test["self_test"], str, "a string")
        if "users" in levels:
            entry["users"] = levels["users"]
        if "count" in levels:
            entry["levels"] = levels["count"]
        if server.get("values"):
            entry["server"] = True
        return entry

    # Several VMs on one page.
    if "baseline" in doc:
        raise ManifestError("with [[vm]] tables, give each VM its own baseline (not one at the top)")
    for k in ("self_test",):
        if k in test:
            raise ManifestError(f"test.{k} is not supported for a lab with several VMs yet")
    if levels or server.get("values") or rename:
        raise ManifestError("[levels], [server] values and overlay.rename are not supported with several VMs yet")
    _type("[[vm]]", vms, list, "a list of [[vm]] tables")
    out, seen = {}, set()
    for i, vm in enumerate(vms):
        where = f"[[vm]] #{i + 1}"
        _keys(where, vm, {"name", "baseline", "memory_mb", "visible"}, required=("name", "baseline"))
        name = _type(f"{where} name", vm["name"], str, "a string")
        if not NAME.match(name) or name in seen:
            raise ManifestError(f"{where}: name {name!r} must be unique, lowercase letters, digits and -")
        seen.add(name)
        item = {"baseline": _type(f"vm {name} baseline", vm["baseline"], str, "a string")}
        if "memory_mb" in vm:
            item["memory_mb"] = _type(f"vm {name} memory_mb", vm["memory_mb"], int, "a whole number")
        if vm.get("visible"):
            item["visible"] = _type(f"vm {name} visible", vm["visible"], bool, "true or false")
        out[name] = item
    entry["identity"] = "polylab"
    entry["overlay"] = {"subdir": ov.get("folder", "rootfs-overlay"), "dest": ov.get("dest", "/"),
                        "permissions": ov.get("permissions", ""), "checksums": ov.get("checksums", "")}
    if replaces:
        entry["overlay"]["replaces"] = replaces
    entry["vms"] = out
    return entry


def is_pin_only(entry):
    """True for a polyci.json entry that defers to the lab's lab.toml."""
    if set(entry) - {"repo", "ref", "vms"}:
        return False
    return all(isinstance(v, dict) and not set(v) - {"repo", "ref"} for v in entry.get("vms", {}).values())


def resolve(lab_id, entry, text=None):
    """A pin-only polyci.json entry -> the full entry, from the lab's lab.toml."""
    if text is None:
        text = fetch(entry["repo"], entry["ref"])
    if text is None:
        raise ManifestError(f"{entry['repo']}@{entry['ref'][:12]} has no lab.toml (or this run can't read "
                            "it); add one to the repo (docs/lab-toml.md), or give the lab its full entry "
                            "in polyci.json")
    full = parse(text, lab_id)
    full["repo"], full["ref"] = entry["repo"], entry["ref"]
    if "vms" not in full:
        if entry.get("vms"):
            raise ManifestError("polyci.json gives vms, but the lab's lab.toml has no [[vm]] tables")
        return full
    pins = entry.get("vms", {})
    unknown = sorted(set(pins) - set(full["vms"]))
    if unknown:
        raise ManifestError(f"polyci.json pins vms {unknown}, which lab.toml does not have")
    for name, vm in full["vms"].items():
        # A VM pinned in polyci.json comes from its own repo; the rest from the lab's repo.
        src = pins.get(name, {"repo": entry["repo"], "ref": entry["ref"]})
        vm["repo"], vm["ref"] = src["repo"], src["ref"]
    del full["repo"], full["ref"]
    full["manifest"] = {"repo": entry["repo"], "ref": entry["ref"]}
    return full


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "show"])
    ap.add_argument("file")
    ap.add_argument("--id", help="the lab id it should have (default: its own id)")
    a = ap.parse_args()
    text = open(a.file, encoding="utf-8").read()
    try:
        lab_id = a.id or tomllib.loads(text).get("id", "")
        entry = parse(text, lab_id)
    except (ManifestError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"::error::{a.file}: {e}")
    if a.command == "show":
        print(json.dumps(entry, indent=2))
    else:
        print(f"{a.file}: ok ({lab_id}, {len(entry.get('vms', {})) or 1} VM)")


if __name__ == "__main__":
    main()
