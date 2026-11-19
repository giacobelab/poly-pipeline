# lab.toml: how a lab describes itself

Each lab repo can carry a `lab.toml` at its root. It says what the lab is
and how to build and test it, so the pipeline holds only a pin for it:

```json
"polylinux-basic": { "repo": "ruffm/polylinux-basic", "ref": "<full commit SHA>" }
```

The pipeline reads `lab.toml` from that repo at that commit. A change to the
lab's build or test settings then lands in the lab's own repo, next to the
change that needs it, and reaches the pipeline when the ref moves.

A lab without a `lab.toml` keeps its full entry in `polyci.json`, as before.
Both kinds can be mixed, so labs move over one at a time.

`lab.json`, the file Nick's labs already have, stays as it is. It holds the
level count, user names and answer form, and `lab.toml` doesn't repeat it.

## One VM (most labs)

```toml
schema = 1
id = "polylinux-basic"          # must match the lab's key in polyci.json
title = "PolyLinux Basic"       # the page title and release name
baseline = "basic"              # the baseline type to build on (see polyci.json "baselines")

[overlay]                       # optional; these are the defaults
folder = "."                    # the repo folder that becomes the lab layer
dest = "/root"                  # where it lands in the image
# permissions = "permissions.txt"   # a Buildroot device table: exact modes and owners
# checksums = "SHA256SUMS"          # every listed file must match
# rename = { "installbandit.sh" = "install.sh" }
# replaces = ["/etc/issue"]         # baseline files the layer replaces on purpose

[levels]                        # optional; default: lab.json's level_count and user_pattern
# count = 10
# users = "level{n}"

[test]
boot = "generic"                # generic | v31 | netsec-snort
# self_test = "test.sh"         # run on the CI machine before building

[server]
# values = true                 # the lab reads /etc/polylab/scenario.* from the PolyLab server
```

## Several VMs on one page

Give each VM a `[[vm]]` table instead of a top-level `baseline`. `[overlay]`
applies to every VM.

```toml
schema = 1
id = "netsec-snort"
title = "Snort Incident Response"

[overlay]
folder = "rootfs-overlay"
dest = "/"
permissions = "permissions.txt"
checksums = "SHA256SUMS"
replaces = ["/etc/hostname", "/etc/inittab", "/etc/issue", "/etc/network/interfaces"]

[test]
boot = "netsec-snort"

[[vm]]
name = "defender"               # lowercase letters, digits, -
baseline = "snort-defender"
memory_mb = 256                 # 128 to 1024; the image unpacks into it
visible = true                  # exactly one VM is the one students see

[[vm]]
name = "attacker"
baseline = "snort-attacker"
memory_mb = 256
```

**Where each VM's files come from:** the repo holding `lab.toml`, unless
`polyci.json` pins that VM to a repo of its own:

```json
"netsec-snort": {
  "repo": "giacobe/netsec-snort-defender", "ref": "<sha>",
  "vms": {
    "attacker": { "repo": "giacobe/netsec-snort-attacker", "ref": "<sha>" },
    "noise":    { "repo": "giacobe/netsec-snort-noise",    "ref": "<sha>" }
  }
}
```

Here the defender comes from the repo with `lab.toml`, and the other two from
their own repos. The weekly update PR moves every one of these refs.

## The three layers

Every VM boots one initrd made of three gzip'd cpio archives, joined end to
end. The kernel unpacks them in order into one RAM filesystem, and a later
file replaces an earlier one with the same path:

| Layer | From | Made by | May contain |
|---|---|---|---|
| 1. baseline | poly-platform | Publish baseline | the OS and its tools |
| 2. lab | the lab repo | Build labs (`make-overlay.py`) | new files anywhere; replaced baseline files only if listed in `overlay.replaces`; nothing under `/etc/polylab/` |
| 3. scenario | poly-server | the page, per student | regular files under `/etc/polylab/` only |

The pipeline checks this on every build:
- **`verify-image.py`** checks the lab layer against the baseline. It
  refuses any undeclared replacement and anything under `/etc/polylab/`.
  It also refuses replacing what boots the VM and runs every command, even
  when declared: `/init`, `/linuxrc`, `/sbin/init`, `/bin/sh`,
  `/bin/busybox`, and existing files under `/lib/` and `/usr/lib/`.
- **`check-scenario.py`** checks every per-student archive CI makes.
  poly-server's own tests check its archives the same way.

A later layer can add or replace files but never delete one. So nothing a
student must not see can be in a lower layer, and nothing secret is ever in
layers 1 or 2.

**Memory:** everything unpacks into RAM, so a VM needs its unpacked size plus
room to work. That's why the Snort VMs have `memory_mb = 256`.

## Checking a lab.toml

From a poly-pipeline checkout:

```sh
python3 scripts/manifest.py check path/to/lab.toml       # is it valid?
python3 scripts/manifest.py show  path/to/lab.toml       # the polyci.json entry it becomes
python3 scripts/plan.py --manifest-dir DIR               # plan with DIR/<lab>.toml in place of the fetched ones
```

The plan job also checks it on every pull request, and reports a missing or
invalid `lab.toml` as an error that names the lab.

`docs/examples/` has a `lab.toml` for each current lab: Basic, PolyBandit
3.1, Networking and Snort.
