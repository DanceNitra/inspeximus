# Contributing to inspeximus

Thank you for reading this far. Contributions that carry a measurement are the ones this project
values most, and the sections below say how to make one land.

## Before you start

- Read the [README](README.md) first screen and the [deep dive](docs/DEEP_DIVE.md).
- Run the suite once: `python -m pytest tests/` (parallel by default; `-n 0` for serial).
- Every published number in this repository has a registry entry and a probe. If your change
  touches a number in `README.md`, run `python claims_audit.py` and then
  `python claims_audit.py --write-claims`. CI fails otherwise.

## What a good pull request contains

1. A test that fails without your change. For a defect fix, add an entry to
   `tools/mutations.json` that breaks the repaired line and name the test that catches it.
   `python tools/mutation_check.py tools/mutations.json` must report 0 survived.
2. No new runtime dependency. `inspeximus/core.py` runs on the standard library alone, and
   `tools/release_check.py` verifies that.
3. A commit message that states what changed and what it was measured against. Dates are
   absolute, never "now" or "recently".
4. English in code, comments, identifiers and docs.

## Challenging a published number

If a number on the README, the project site or a GitHub comment does not reproduce for you, open
an issue with the **Challenge a published number** template. Include the command you ran, the
version (`pip show inspeximus`), the platform, and the value you got. A challenge that holds gets
the number corrected in the next release with credit to you in the changelog.

## Good first issues

Issues labelled `good first issue` are scoped to one file and one test. Ask in the issue before
starting so two people do not build the same thing.

## Releasing

Maintainers only. The procedure is in [RELEASING.md](RELEASING.md); `tools/release_check.py` is
the gate and nothing ships until it exits 0.

## Reporting a vulnerability

See [SECURITY.md](SECURITY.md). Do not open a public issue for a security defect.
