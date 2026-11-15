# PolyCI

Build, test and release pipeline for PolyLinux labs. PolyCI holds no lab code:
it checks each lab repo out at a pinned commit, packages it onto a tested
Buildroot baseline, tests the result, and publishes it as a release and a
playable browser page.

```
 polyci.json ──► baseline.yml ──► baseline-<type>-<hash>          (kernel + base rootfs)
                                         │
 lab repo @ pinned commit ──► make-overlay.py ──► lab.cpio.gz     (lab files under /root)
                                         │
              packaged.rootfs.cpio.gz = baseline rootfs.cpio.gz + lab.cpio.gz
                                         │
           image check ─► QEMU boot test ─► browser play test (v86)
                                         │
                 <lab>-<commit>-<baselinehash>-release  +  GitHub Pages
```

## How it fits together

**Baselines** are Buildroot builds of a kernel and base rootfs with no lab in
them, one per *type* (`basic`, `filesystem`, `networking`, `netsec`, ...). Each
is published once as a release tagged `baseline-<type>-<hash>`, where the hash
covers the Buildroot version, the whole config set and the builder's build
script. Rebuilding with the same inputs gives the same tag; changing anything
gives a new one.

**Labs** are Nick's repos (or yours), each pinned to a commit in
`polyci.json`. PolyCI turns the lab's payload folder into `lab.cpio.gz`:
files from the commit itself, under `/root`, with the same modes and
line-ending handling as buildroot-builder2's `03-package-payload.sh`, and
byte-for-byte reproducible.

**Joining** is plain concatenation. The Linux kernel unpacks concatenated
initramfs archives in order, so the lab's files land on top of the base:

```sh
cat baseline/rootfs.cpio.gz lab.cpio.gz > packaged.rootfs.cpio.gz
```

The baseline's bytes are untouched, so its checksum still proves exactly which
tested base is inside every lab image. Nothing is unpacked or repacked.

**Releases** are tagged `<lab>-<first 7 of lab commit>-<baseline hash>-release`,
e.g. `polybandit3.1-0c95f01-3f9a1c2b7d-release`, with:

| File | What it is |
|---|---|
| `packaged.bzImage` | the baseline's kernel, unchanged |
| `packaged.rootfs.cpio.gz` | baseline rootfs + `lab.cpio.gz` |
| `lab.cpio.gz` | the lab overlay alone |
| `lab-manifest.txt` | every lab file: mode, path, SHA-256 |
| `release.json` | lab repo + commit, baseline tag + checksums, the run that built it |
| `SHA256SUMS` | checksums of all of the above |

A new release appears only when a lab commit or its baseline changes.

## `polyci.json`

```jsonc
{
  "builder":   { "repo": "giacobe/buildroot-builder2", "ref": "<sha>", "buildroot_version": "2025.02.15" },
  "v86":       { "npm_version": "0.5.473", "bios_repo": "copy/v86", "bios_ref": "<sha>" },
  "baselines": {
    "filesystem": { "config_set": "basic-compression", "release": "baseline-filesystem-<hash>" },
    "networking": { "config_set": "networking-prototype-v1",
                    "from_lab": { "repo": "giacobe/polylinux-networking", "ref": "<sha>",
                                  "install_script": "install-builder-config.sh" },
                    "release": "" }
  },
  "labs": {
    "polybandit3.1": { "title": "PolyBandit 3.1", "repo": "giacobe/polybandit3.1", "ref": "<sha>",
                       "subdir": ".", "baseline": "filesystem", "self_test": "test.sh",
                       "boot_test": "v31", "users": "bandit{n}", "levels": 13 }
  }
}
```

- `baselines.<type>.config_set` is a builder config set, or one a lab repo
  provides (`from_lab`: PolyCI runs that repo's install script into the
  builder, as Nick's instructions say).
- `baselines.<type>.release` pins the promoted baseline release labs use.
  Blank: labs of that type are skipped (reported, not failed).
- `labs.<id>.subdir` is the folder that becomes `/root`; `rename` renames files
  inside it (e.g. `installbandit.sh` → `install.sh`).
- `labs.<id>.boot_test`: `v31` checks PolyBandit 3.1 answers and screens
  exactly; `generic` works for any PolyLinux lab (installer logs in as the
  first user, `nextlevel` reaches every level's user).
- `labs.<id>.self_test`: optional script in the lab repo to run first (e.g.
  `test.sh`).

## Workflows

| Workflow | When | What |
|---|---|---|
| **Publish baseline** (`baseline.yml`) | by hand, per type | Build, smoke-boot, publish `baseline-<type>-<hash>` as a pre-release |
| **Build labs** (`labs.yml`) | push, PR, weekly, by hand | Per lab: overlay, join, test; on `main`: releases + Pages |

The required check for branch protection is **`all labs pass`**: one check,
however many labs there are.

## Day-to-day

**First baseline of a type**
1. Actions → **Publish baseline** → Run workflow → pick the type. About an
   hour; publishes a pre-release.
2. Actions → **Build labs** → Run workflow with `baseline_override` =
   `TYPE=TAG`. All labs of that type must pass. Nothing is published.
3. Promote: `gh release edit TAG --prerelease=false --latest=false`.
4. Pin it: set `baselines.TYPE.release` to `TAG` in `polyci.json` in a PR.
   Merging releases every lab of that type and updates Pages.

**A lab changed upstream:** update its `ref` in `polyci.json` in a PR. The PR
runs the full pipeline; merging publishes the new release.

**A new lab:** add an entry to `labs`. Use `"boot_test": "generic"` unless it
needs exact answer checks.

## Pages

Every publishing run deploys `https://<you>.github.io/PolyCI/` with a
landing page and one playable page per lab (`/<lab-id>/`). One-time setup:
**Settings → Pages → Source: GitHub Actions**.

## Running pieces locally

```sh
python3 scripts/plan.py                                   # what CI will build
python3 scripts/make-overlay.py --repo ../polybandit3.1 --ref <sha> --out lab.cpio.gz --manifest m.txt
cat rootfs.cpio.gz lab.cpio.gz > packaged.rootfs.cpio.gz
python3 scripts/boot-test.py --flavor v31 --kernel bzImage --initrd packaged.rootfs.cpio.gz
```

The boot test needs `qemu-system-i386` and `python3-pexpect`.
