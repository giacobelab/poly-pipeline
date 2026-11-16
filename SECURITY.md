# Security Policy

## Supported versions

Only the `main` branch and the releases it currently pins in `polyci.json` are
supported. Older lab and baseline releases are not patched; they are replaced.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub: **Security** tab → **Report a
vulnerability**. If that is not available, email **[CONTACT EMAIL]**.

Include what you found, how to reproduce it, and what an attacker could do with
it. You should get an acknowledgement within 7 days and an update on a fix
within 30 days.

## What counts

In scope, for this repository:

- The GitHub Actions workflows: anything that could leak `GITHUB_TOKEN` or
  `POLYCI_BOT_TOKEN`, let a pull request from a fork run code with write
  access, or publish a release or Pages site that was not built and tested by
  the pipeline.
- The scripts: for example a crafted lab repository that makes
  `make-overlay.py` write outside its output, or a release that passes
  `verify-image.py` without matching what was tested.
- The published GitHub Pages site (for example script injection).
- Outdated software in the baseline images with a known vulnerability that
  matters outside the VM (the labs run in the browser or in local VMs, isolated
  from the host).

Out of scope:

- The lab challenges themselves. Levels are meant to be broken into inside
  the VM; finding a way to skip a level is a lab bug, not a security issue.
  Report it to the lab's own repository instead.
- Problems in Buildroot, the Linux kernel, v86 or another upstream project.
  Report those upstream; a PolyCI fix (such as moving a pinned version) is
  welcome as a normal pull request once the issue is public.
