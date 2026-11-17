# PolyCI

Build, test and release pipeline for PolyLinux labs. PolyCI holds no lab code:
it checks each lab repo out at a pinned commit, packages it onto a tested
Buildroot baseline, tests the result, and publishes it as a release and a
[playable browser page](https://ruffm.github.io/PolyCI/).

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
them, one per *type* (`basic`, `filesystem`, `networking`, `snort-defender`,
...). Each is published once as a release tagged `baseline-<type>-<hash>`,
where the hash covers the Buildroot version, the whole config set, the
builder's shared `config-common/` files and its build script. Only file
contents count, not which repo they came from. Rebuilding with the same inputs gives the same tag; changing anything
gives a new one.

**Labs** are pinned to a commit in
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

## Multi-VM labs (netsec-snort)

Some labs are several VMs that work together on one page. The Snort lab has
a **defender** the student uses, plus an **attacker** and a **noise** VM that
generate traffic in the background. They need:

- **Two baselines**: `snort-defender` for the defender and `snort-attacker`
  for the other two, from the builder's `snort-defender` and `snort-attacker`
  config sets.
- **Overlays at `/`**: each VM repo's `rootfs-overlay/` is placed at the image
  root, with modes and owners from its `permissions.txt` (setuid included) and
  every file checked against its `SHA256SUMS`.
- **One network**: the defender is 10.0.0.10; the others pick addresses from
  the student's identity and send traffic to it.
- **One identity**: every VM gets the same `polylab_email_hex`, `polylab_date`
  and `polylab_code` on its kernel command line.

**The page** (`web/multi.html`) asks for the email once and starts the
defender on the page, with the screen and keyboard. Each hidden VM runs in its
own Web Worker (`web/vm-worker.js`), so it gets its own CPU core, and starts a
few seconds after the one before it. The page passes every Ethernet frame a VM
sends to all the others, like a hub. Every VM boots with `net.ifnames=0`, so
its card stays `eth0` (the Snort baselines use udev, which would rename it to
`enp0s3`, and the labs' scripts wait for `eth0`). Nothing leaves the page, and other tabs
are not connected. The exercise date is today, unless the link sets
`?date=YYYY-MM-DD` (e.g. for a whole class).

**The tests**: `boot-test-multi.py` boots all the VMs in QEMU on one network
(joined by a small hub in the script). It checks that each VM logs in and
shows its memory, that frames flow from every VM, and that the defender has
its address, sees at least two other hosts, and got the email. With tcpdump
in the image, it also checks the defender captures packets from at least
three other addresses. `page-test-multi.mjs` does the same in Chrome through
the real page.

**The release**, `<lab>-<7 hex of the VM commits>-<10 hex of the baseline
tags>-release`, has `<vm>.bzImage`, `<vm>.rootfs.cpio.gz`, `<vm>.lab.cpio.gz`
and `<vm>.lab-manifest.txt` for each VM, plus `release.json` and `SHA256SUMS`.

## `polyci.json`

```jsonc
{
  "builder":   { "repo": "ruffm/buildroot-builder2", "ref": "<sha>", "buildroot_version": "2025.02.15" },
  "v86":       { "npm_version": "0.5.473", "bios_repo": "copy/v86", "bios_ref": "<sha>" },
  "baselines": {
    "basic":      { "config_set": "basic", "release": "" },
    "filesystem": { "config_set": "basic-compression", "release": "baseline-filesystem-<hash>" },
    "networking": { "config_set": "networking-prototype-v1",
                    "from_lab": { "repo": "giacobe/polylinux-networking", "ref": "<sha>",
                                  "install_script": "install-builder-config.sh" },
                    "release": "" }
  },
  "labs": {
    "polybandit3.1": { "title": "PolyBandit 3.1", "repo": "giacobe/polybandit3.1", "ref": "<sha>",
                       "subdir": ".", "baseline": "filesystem", "self_test": "test.sh",
                       "boot_test": "v31" },
    "polylinux-networking": { "...": "...", "users": "level{n}", "levels": 10 }
  }
}
```

- `builder` is the buildroot-builder2 to build baselines with. It points at
  the fork `ruffm/buildroot-builder2` (branch `polyci`) while its changes are
  pull requests to `giacobe/buildroot-builder2`: the basic set's BusyBox fix,
  no host QEMU, dropped-option and command checks, the Snort config sets and
  rootfs-overlay packaging, shared `config-common/` settings, and Bootlin's
  prebuilt i686 toolchain (instead of compiling GCC and glibc every build). Once Nick
  merges them, point `repo` and `ref` back at his repository. If the merged
  files are the same, the baseline tags stay the same.
- `baselines.<type>.config_set` is a builder config set, or one a lab repo
  provides (`from_lab`: PolyCI runs that repo's install script into the
  builder, as Nick Giacobe's instructions say).
- `baselines.<type>.extra_config`: extra `BR2_...` lines added to the end of
  the config set's `features.fragment` before the build (and so into the tag
  hash), for a fix the builder does not have yet. `drop_config` lists
  options whose lines are removed first. The networking set (from
  `polylinux-networking`) uses both to switch to the prebuilt toolchain the
  builder's own sets get from `config-common/`.
- Every baseline build runs `scripts/check-baseline.py` before publishing: each
  `BR2_...=y` the config set asks for must still be set after Buildroot's
  `olddefconfig`, and each command in `required-commands.txt` must be in the
  rootfs.
- `baselines.<type>.release` pins the promoted baseline release labs use.
  Blank: labs of that type are skipped (reported, not failed).
- `labs.<id>.vms` makes a multi-VM lab (see above): one entry per VM, with
  `repo`, `ref`, `baseline`, `memory_mb` (128–1024; the image unpacks into it,
  so the Snort VMs need 256), and `"visible": true` on
  exactly one. `overlay` sets the folder (`subdir`), where it goes (`dest`),
  and the repo's `permissions` and `checksums` files. The weekly update PR
  moves each VM's `ref` on its own.
- `baselines.<type>.from_lab` takes either `install_script` (run the lab's
  installer into the builder) or `config_dir` (copy that folder in as the
  config set).
- `labs.<id>.subdir` is the folder that becomes `/root`; `rename` renames files
  inside it (e.g. `installbandit.sh` → `install.sh`).
- `labs.<id>.boot_test`: `v31` checks PolyBandit 3.1 answers and screens
  exactly; `generic` works for any PolyLinux lab (installer logs in as the
  first user, `nextlevel` reaches every level's user).
- `labs.<id>.self_test`: optional script in the lab repo to run first (e.g.
  `test.sh`).
- Level users and the number of levels come from the lab's own `lab.json`
  (`user_pattern`, `level_count`). Set `users` / `levels` here only for a lab
  without a `lab.json` (like polylinux-networking), or to override it; a
  mismatch with `lab.json` shows as a warning.

## Workflows

| Workflow | When | What |
|---|---|---|
| **Publish baseline** (`baseline.yml`) | by hand, per type | Build, smoke-boot, publish `baseline-<type>-<hash>` as a pre-release |
| **Build labs** (`labs.yml`) | push, PR, weekly, by hand | Per lab: overlay, join, test; on `main`: releases + Pages; weekly: then clean up releases |
| **Propose lab updates** (`update-labs.yml`) | weekly, by hand | PR moving lab refs to their repos' newest commits |
| **Clean up releases** (`cleanup.yml`) | by hand; weekly from Build labs | Delete old baseline and unused releases |

The required check for branch protection is **`all labs pass`**: one check,
however many labs there are.

### What "Build labs" does per lab

`scripts/plan.py` gives each lab a mode, shown in the run summary:

| Mode | When | What runs | Time |
|---|---|---|---|
| `build` | new lab commit or baseline, PolyCI's pipeline changed, weekly run, or **force** | overlay, join, image check, then boot test (QEMU) and page test (Chrome) in parallel; publish on `main` | minutes |
| `reuse` | `<lab>-<commit>-<hash>-release` exists and was made by this exact pipeline | download the released images, rebuild the page | seconds |
| `skip` | no baseline pinned for the lab's type | nothing | — |

"This exact pipeline" is a fingerprint, recorded in each release's
`release.json`, of `scripts/`, `web/`, `tools/Dockerfile`, `labs.yml`, the v86
settings and the lab's `polyci.json` entry. Change any of them and affected
labs rebuild and retest. Packaging is reproducible, so if the rebuilt images
are byte-identical, the existing release just gets the new fingerprint; a
release's images are never replaced.

The QEMU boot test runs in a prepared image, `ghcr.io/ruffm/polyci-qemu`,
built from `tools/Dockerfile` by the `tools` job only when that file changes.
The page test uses the runner's Chrome with a cached `puppeteer-core`.

## Day-to-day

**First baseline of a type**
1. Actions → **Publish baseline** → Run workflow → pick the type. Publishes
   a pre-release; the run's summary says what to do next.
2. Actions → **Build labs** → Run workflow with `baseline_override` = the
   type (e.g. `networking`: its newest release) and tick **promote**. After
   publishing several types, `latest` tests every type whose newest release is
   newer than its pin. Several types, or exact tags (`baseline-networking-…`,
   `TYPE=TAG`), can be comma-separated. Leave `labs` blank or name labs that
   use those baselines. The run's summary lists pinned → tested for each type.
   Nothing is published from this run.
3. If every lab passes, the **Promote and pin tested baselines** job marks the
   baseline as a full release and opens a PR (`polyci/pin-baselines`) setting
   `baselines.TYPE.release` in `polyci.json`. Merge it: that releases every lab
   of that type and updates Pages. (With only the built-in token, close and
   reopen the PR to start its checks, or add `POLYCI_BOT_TOKEN`.)

   Without **promote**, do it by hand:
   `gh release edit TAG --prerelease=false --latest=false`, then set
   `baselines.TYPE.release` in a PR.

**A lab changed upstream:** every Monday, **Propose lab updates** opens (or
refreshes) one PR moving each changed lab's `ref` to its newest commit, with
compare links. The PR runs the full pipeline; merging publishes the new
releases. Can also run it by hand.

Pull requests opened with the built-in token don't start other workflows. To
have CI run on these PRs automatically, add a repository secret
`POLYCI_BOT_TOKEN`: a fine-grained personal access token for this repository
with **Contents** and **Pull requests** set to read and write. 

Without it, close
and reopen the PR to start CI. Either way, allow Actions to open PRs:
**Settings → Actions → General → Allow GitHub Actions to create and approve
pull requests**.

**Rebuild everything:** **Build labs** → Run workflow with **force**.

**A new lab:** add an entry to `labs`. Use `"boot_test": "generic"` unless it
needs exact answer checks.

**Old releases:** **Clean up releases** → Run workflow. Left unticked,
**delete** only lists what it would delete (see the run's summary); tick it
to delete those releases and their tags. Pinned baselines and every lab's
current release are always kept, plus the newest `keep_previous` older ones
per baseline type and per lab (for rollback) and anything younger than
`min_age_days`. Releases of labs or baseline types no longer in `polyci.json`
go. Tags that do not look like PolyCI releases are never touched. The
weekly **Build labs** run does the same with **delete** and the defaults,
once every lab has passed and the site is deployed.

**The Snort lab, first time:** run **Publish baseline** for `snort-defender`
and for `snort-attacker`. Then run **Build labs** with labs `netsec-snort`,
baseline_override `snort-defender,snort-attacker` and **promote** ticked.
Merge the pin PR it opens.

## Pages

Every publishing run deploys `https://ruffm.github.io/PolyCI/` with a
landing page and one playable page per lab (`/<lab-id>/`). One-time setup:
**Settings → Pages → Source: GitHub Actions**.

## Running pieces locally

```sh
python3 scripts/plan.py                                   # what CI will build
python3 scripts/make-overlay.py --repo ../polybandit3.1 --ref <sha> --out lab.cpio.gz --manifest m.txt
cat rootfs.cpio.gz lab.cpio.gz > packaged.rootfs.cpio.gz
python3 scripts/boot-test.py --flavor v31 --kernel bzImage --initrd packaged.rootfs.cpio.gz
```

The boot test needs `qemu-system-i386` and `python3-pexpect`. For a multi-VM
lab, `scripts/group.py build` makes the images (it needs `gh`), and
`scripts/boot-test-multi.py --images out` tests them.

## Contributing, security and license

See [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md)
and [SECURITY.md](SECURITY.md). PolyCI's own files are under the
[MIT License](LICENSE). The labs belong to their authors (PolyBandit and the
PolyLinux labs are by Nick Giacobe) and keep their own licenses; the images
contain Buildroot packages under their own licenses.
