# Releasing inspeximus

What ships alone, what ships batched, and the one condition that applies to both.

This is written from what actually went wrong, not from a general principle.

---

## The rule

**Batching is not the risk. Shipping a fix without a mutation-verified test is the risk.**

Every defect found on 2026-07-26 was created *inside the previous fix*, not by batch size:

- 1.67.0 removed a false tamper alarm and opened a laundering path: a public `slash()` flipped
  `verify_writes()` from False back to True with forged text standing.
- The first version of the 1.68.0 repair bound text+key on every receipt but still forgave **attribution**
  — the one thing committing attribution exists to catch. Dropping that check survived all 541 tests.
- The 1.71.0 atomicity test passed against the truncating implementation it was written to reject, because
  it only asserted "no leftover temp file".

A bigger batch would not have caused any of those. A smaller one would not have caught them. Mutation did.

## But version count has a real cost for THIS product

Measured, 1.51.0 alongside 1.71.0 on one store file:

```
1.51.0 opens a handle -> 1.71.0 writes and flushes -> 1.51.0 flushes
final: ['baseline record', 'written by OLD after the fact']   <- the newer write is GONE, silently
```

The single-writer guard shipped in **1.67.0**; older handles do not have it and save anyway. So every
extra published version widens the window for a partially-upgraded fleet, which is the dangerous state —
not the fully-old one. See SECURITY.md. (44 published versions is also what broke the MCP registry check:
it read page one of a 30-per-page API and could no longer see the version it had just released.)

So: fewer releases is better here, and the reason is specific to us.

## What ships how

| class | ship | today's examples |
|---|---|---|
| **Data loss / security** | **alone, immediately** | 1.68.0 (tamper laundering), 1.71.0 (installer overwrote `settings.json`) |
| **Behaviour change** | alone, with a `BEHAVIOUR CHANGE` line at the top of the entry | 1.70.0 (`valid` now requires the absence proof to have run) |
| **Ordinary fix + coverage** | **batched** | 1.69.0 and 1.70.0 could have been one release |
| **Tests only** | **no release at all** | three commits on 2026-07-26 shipped tests with no version bump |

For a data-loss or security fix, every extra hour is a cost the user pays. For anything else, the cost is
ours and the user's version count is theirs.

## The condition on every fix, batched or not

Each fix in a release carries **its own test, whose teeth are verified by mutating the repaired logic**.
Not "the suite is green" — green is what a broken instrument looks like. Concretely:

1. The suite is green **before** the first mutant (an overlapping run once left three files mutated, so
   every mutant looked killed and the harness reported 92.5% where the truth was ~36%).
2. Mutate the line the fix touched; the test that covers it must FAIL, and you must check *which* test
   failed — two "SURVIVES" reports were mis-located mutations, and one was a bad substitute.
3. The runner counts `ERROR` as caught, not only `FAILED`. A mutant that breaks the import is caught, and
   a runner that greps for `FAILED` alone reports it as surviving.
4. If the mutant survives, the test does not test the fix. Ask what the fixture **cannot express** before
   assuming the code is fine. Real examples: a non-hex nonce made the intent malformed so assertions behind
   `if res["ok"]:` were unreachable; a bare signature string is skipped as malformed so `both_cosigned`
   read False on both branches; `slash()` zeroes `good`, so a bare slashed record is dropped by the
   corroboration term and the retraction term is never exercised.

This is what lets a batch be safe: when something breaks, each fix is isolable by its own failing test
without having to split the release.

## Mechanics

```bash
python -m pytest -q                       # green first, always
# bump BY HAND exactly ONE file -- pyproject.toml is the source of truth:
#   version = "X.Y.Z"
# then PIN everything else -- do not hand-edit any of them:
python packages/_pin_server_json.py       # server.json, .claude-plugin/{plugin,marketplace}.json,
                                          # inspeximus/core.py __version__, README.md vX.Y.Z badges
# CHANGELOG.md: newest entry on top; lead with who should upgrade and why
python tools/mutation_check.py tools/mutations.json    # must exit 0: 0 survived AND 0 skipped
git commit && git tag vX.Y.Z && git push origin main && git push origin vX.Y.Z
```

**This checklist said "core.py, pyproject.toml, README.md footer" and 1.86.0 shipped with CI red.**
There are FIVE version fields across four files, not three, and `tests` asserts every one of them
against `pyproject.toml`. The pinner exists precisely so nobody has to remember them — it was in the
release workflow but nowhere in the human procedure, so the manifests were stale in the commit the
tests ran on. (`release` does not depend on `tests`, so the package published anyway: the wheel was
correct, the registry manifests advertised 1.85.0.) A step that only a human remembers is a step that
gets skipped; that is what the pinner is for, and it now appears where the human is looking.

**And on 1.89.0 the same class bit a THIRD time, one layer in.** The checklist above still asked a human
to bump `inspeximus/core.py` by hand and called the README badge "still manual" — so bumping
`pyproject.toml` and running the pinner left `core.py` at the previous version, which is the number
`import inspeximus; inspeximus.__version__` returns to a user. Only
`test_the_package_version_is_the_one_source_of_truth` caught it. The pinner now covers both, the
checklist names exactly one file to edit by hand, and two tests assert the pinner's *behaviour* on a
copied tree (plus a control that it REFUSES when the `__version__` assignment goes missing, rather than
quietly pinning nothing). Both new tests fail against the pre-1.89.0 pinner, which is the only evidence
that they test anything.

Then **verify from PyPI, not from the repo** — install the published wheel in a clean venv and exercise
the fix. The repo passing proves the repo passes.

The release workflow publishes to PyPI (trusted, attested) and then checks the MCP registry lists the new
version. If that last step fails, look at the *reader* before assuming the publish failed: 1.68.0 published
correctly and the registry already marked it latest while our own check reported it missing.

## Never

- Never anchor a test to a line number. The recorded mutation-survivor lines were stale within a day:
  what was `core.py:3662` became a comment.
- Never retag a published version to make a red CI run green. 1.68.0's tag stays red; the fix landed on
  `main` and proved itself on the next release.
- Never `git checkout --` / anything that rewrites the working tree with an uncommitted fix in it. That
  destroyed a fix once and it had to be written twice.
