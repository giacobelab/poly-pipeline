#!/usr/bin/env python3
"""Build, fetch and describe a multi-VM lab (labs.<id>.vms in polyci.json).

  group.py build   --group FILE --out DIR [--work DIR]
  group.py fetch   --group FILE --out DIR --repo OWNER/REPO
  group.py release --group FILE --out DIR --dist DIR

FILE is the lab's entry from plan.py's groups matrix (JSON).

build    for every VM: download its baseline release (each tag once), fetch
         the VM's repo at its pinned commit, build its overlay
         (make-overlay.py), join it onto the baseline and verify the result.
         Writes, per VM: <vm>.bzImage, <vm>.rootfs.cpio.gz, <vm>.lab.cpio.gz,
         <vm>.lab-manifest.txt; and vms.json (what the page and tests read).
fetch    download those images from the lab's existing release instead.
release  write release.json and SHA256SUMS into --dist, next to the images.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(*cmd, **kw):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gh_repo():
    return os.environ["GITHUB_REPOSITORY"]


def vms_json(group, out):
    """The page and the tests read this: one entry per VM, visible one first."""
    vms = sorted(group["vms"], key=lambda v: not v["visible"])
    info = [{"name": v["name"], "visible": v["visible"], "memory_mb": v["memory_mb"],
             "bzimage": f"{v['name']}.bzImage", "initrd": f"{v['name']}.rootfs.cpio.gz"} for v in vms]
    (out / "vms.json").write_text(json.dumps(info, indent=2) + "\n")


def build(a, group):
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    bases = {}
    for v in group["vms"]:
        tag = v["baseline_tag"]
        if tag in bases:
            continue
        d = work / "base" / tag
        d.mkdir(parents=True, exist_ok=True)
        pre = subprocess.run(["gh", "release", "view", tag, "-R", gh_repo(), "--json", "isPrerelease",
                              "--jq", ".isPrerelease"], capture_output=True, text=True)
        if pre.returncode != 0:
            sys.exit(f"::error::Baseline release {tag} not found")
        if pre.stdout.strip() == "true" and not v["baseline_is_override"]:
            sys.exit(f"::error::{tag} is still a pre-release. Promote it before pinning it in polyci.json.")
        run("gh", "release", "download", tag, "-R", gh_repo(), "-D", d, "--clobber",
            "-p", "bzImage", "-p", "rootfs.cpio.gz", "-p", "baseline-manifest.tar.gz",
            "-p", "baseline.json", "-p", "SHA256SUMS")
        run("sha256sum", "-c", "SHA256SUMS", cwd=d)
        run("tar", "-xzf", "baseline-manifest.tar.gz", cwd=d)
        bases[tag] = d

    for v in group["vms"]:
        name, base = v["name"], bases[v["baseline_tag"]]
        src = work / "src" / name
        if src.exists():
            shutil.rmtree(src)
        run("git", "init", "-q", src)
        url = os.environ.get("POLYCI_GIT_URL", "https://github.com/{repo}").format(repo=v["repo"])
        run("git", "-C", src, "fetch", "-q", "--depth", "1", url, v["ref"])
        cmd = [sys.executable, HERE / "make-overlay.py", "--repo", src, "--ref", v["ref"],
               "--subdir", group["overlay_subdir"], "--dest", group["overlay_dest"],
               "--out", out / f"{name}.lab.cpio.gz", "--manifest", out / f"{name}.lab-manifest.txt"]
        if group["overlay_permissions"]:
            cmd += ["--permissions", group["overlay_permissions"]]
        if group["overlay_checksums"]:
            cmd += ["--checksums", group["overlay_checksums"]]
        run(*cmd)
        with open(out / f"{name}.rootfs.cpio.gz", "wb") as fh:
            fh.write((base / "rootfs.cpio.gz").read_bytes() + (out / f"{name}.lab.cpio.gz").read_bytes())
        shutil.copyfile(base / "bzImage", out / f"{name}.bzImage")
        verify = [sys.executable, HERE / "verify-image.py", "--packaged", out / f"{name}.rootfs.cpio.gz",
                  "--base", base / "rootfs.cpio.gz", "--lab", out / f"{name}.lab.cpio.gz",
                  "--manifest", out / f"{name}.lab-manifest.txt"]
        req = base / "manifest" / "required-commands.txt"
        if req.exists():
            verify += ["--required", req]
        print(f"::group::Verify {name}", flush=True)
        run(*verify)
        print("::endgroup::", flush=True)
        (out / f"{name}.baseline.json").write_bytes((base / "baseline.json").read_bytes())
    vms_json(group, out)


def fetch(a, group):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pats = ["release.json", "SHA256SUMS"]
    for v in group["vms"]:
        pats += [f"{v['name']}.bzImage", f"{v['name']}.rootfs.cpio.gz"]
    run("gh", "release", "download", group["release_tag"], "-R", a.repo, "-D", out, "--clobber",
        *[x for p in pats for x in ("-p", p)])
    run("sha256sum", "-c", "--ignore-missing", "SHA256SUMS", cwd=out)
    vms_json(group, out)


def release(a, group):
    out, dist = Path(a.out), Path(a.dist)
    dist.mkdir(parents=True, exist_ok=True)
    e = os.environ
    files, vms = [], {}
    for v in group["vms"]:
        n = v["name"]
        for suffix in ("bzImage", "rootfs.cpio.gz", "lab.cpio.gz", "lab-manifest.txt"):
            shutil.copyfile(out / f"{n}.{suffix}", dist / f"{n}.{suffix}")
            files.append(f"{n}.{suffix}")
        base = json.loads((out / f"{n}.baseline.json").read_text())
        vms[n] = {
            "repo": v["repo"], "commit": v["ref"], "visible": v["visible"], "memory_mb": v["memory_mb"],
            "baseline": {"tag": v["baseline_tag"], "type": v["baseline_type"],
                         "kernel_sha256": base["kernel_sha256"], "rootfs_sha256": base["rootfs_sha256"]},
            "lab_cpio_sha256": sha(dist / f"{n}.lab.cpio.gz"),
            "packaged_rootfs_sha256": sha(dist / f"{n}.rootfs.cpio.gz"),
        }
    info = {
        "tag": group["release_tag"], "lab": group["lab"], "title": group["title"], "vms": vms,
        "overlay": {"folder": group["overlay_subdir"], "dest": group["overlay_dest"],
                    "permissions": group["overlay_permissions"]},
        "join": "<vm>.rootfs.cpio.gz = baseline rootfs.cpio.gz + <vm>.lab.cpio.gz (byte concatenation)",
        "fingerprint": group["fingerprint"],
        "polyci_commit": e.get("GITHUB_SHA", ""),
        "built_by": f'{e.get("GITHUB_SERVER_URL", "")}/{e.get("GITHUB_REPOSITORY", "")}/actions/runs/{e.get("GITHUB_RUN_ID", "")}',
    }
    (dist / "release.json").write_text(json.dumps(info, indent=2) + "\n")
    files.append("release.json")
    (dist / "SHA256SUMS").write_text("".join(f"{sha(dist / f)}  {f}\n" for f in files))
    print((dist / "release.json").read_text())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["build", "fetch", "release"])
    ap.add_argument("--group", required=True, help="JSON file: this lab's entry in plan.py's groups matrix")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default="group-work")
    ap.add_argument("--dist")
    ap.add_argument("--repo")
    a = ap.parse_args()
    group = json.loads(Path(a.group).read_text())
    {"build": build, "fetch": fetch, "release": release}[a.command](a, group)


if __name__ == "__main__":
    main()
