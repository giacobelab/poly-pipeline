# Contributing to PolyCI

Thanks for helping. PolyCI is the build-and-test pipeline for the PolyLinux
labs: it builds baseline images with Buildroot, adds each lab on top, tests the
result, and publishes releases and browser (v86) pages. See the
[README](README.md) for how it fits together.

## Where does my change go?

| You want to change | Where |
|---|---|
| A lab's levels, text, helpers or `lab.json` | The lab's own repository (for example `giacobe/polybandit3.1`). Once merged there, PolyCI's weekly **Propose lab updates** PR picks it up. |
| The packages or kernel in a baseline | The config sets in `giacobe/buildroot-builder2` (or the lab repo that provides one). A temporary fix can go in `polyci.json` as `extra_config`. |
| How labs are built, tested or published; the web page; adding a lab or baseline type | Here. |

Please do not post lab solutions or level passwords in issues or pull
requests; see the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and asking for things

Use the issue forms (**Issues → New issue**). For a failed run, include the
link to the run and the first error. Security problems go through
[SECURITY.md](SECURITY.md), not issues.

## Making a change

1. Fork the repository (or make a branch, if you have access) and branch from
   `main`.
2. Make the change. Keep pull requests to one topic.
3. Check it locally where you can:
   - `actionlint` on any workflow you changed;
   - `python3 scripts/plan.py` (it validates `polyci.json` and prints the plan);
   - `python3 -m py_compile scripts/*.py`.
4. Open a pull request using the template. **Build labs** runs on it, and the
   **all labs pass** check must be green before it can be merged.
   Pull requests from forks build and test but never publish.
5. Update `README.md` when you change how something is used.

### Pinning rules

- Lab `ref`s and the builder `ref` are full 40-character commit SHAs, never
  branch names.
- A new baseline is published as a pre-release, tested with **Build labs**
  (`baseline_override`, tick **promote**), and only then pinned. Do not pin an
  untested baseline by hand.
- Actions are pinned to a major version and kept current by Dependabot.

### Style

- Shell: `bash` with `set -euo pipefail` in anything longer than a few lines;
  quote variables.
- Python: standard library only (the runners and the tools image have nothing
  else), Python 3.10+.
- Workflows: least privilege. `permissions: contents: read` by default; a job
  asks for more only if it publishes.
- Commit messages: a short summary line, then what changed and why.

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE) that covers this repository.
