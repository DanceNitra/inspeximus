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
                                          # inspeximus/core.py __version__, CITATION.cff,
                                          # README.md vX.Y.Z badges
# CHANGELOG.md: newest entry on top; lead with who should upgrade and why
python tools/mutation_check.py tools/mutations.json    # must exit 0: 0 survived AND 0 skipped
python tools/release_check.py             # THE GATE. Must exit 0. Nothing below runs until it does.
python tools/release_notes.py --out NOTES.md          # the GitHub release body, from the changelog
git commit && git tag vX.Y.Z && git push origin main && git push origin vX.Y.Z
```

**`python tools/release_check.py` is the checklist above as code**, because a step only a human
remembers is a step that gets skipped — which is the sentence this file has now had to write four
times. It checks, in one run: every version field across `pyproject.toml`, `inspeximus/core.py`,
`CITATION.cff`, `server.json`, both `.claude-plugin` manifests, the README badge and `glama.json`;
that `import inspeximus` **from this tree** reports that same version; that the changelog has an entry
for it and that the entry is the newest; that the package still declares zero dependencies **and still
imports and runs with every non-stdlib import blocked**; that the MCP server imports; that the release
notes build and their example actually runs; and the suite.

Its exit code is the contract, and an unrun check is not a passing one:

| exit | meaning |
|---|---|
| **0** | every check ran and passed — the only state that clears a release |
| **1** | at least one check FAILED |
| **2** | nothing failed, but something was SKIPPED (a missing `mcp` extra, or `--skip-tests`) |

**It calls the audits rather than growing a second copy of them.** `claims_audit.py` and
`governance_audit.py` already own "is every published claim still true?", including the counts quoted
in `README.md`, `MCP_LISTINGS.md` and `index.html`, and `release.yml` runs them before it publishes.
The checklist shells out to the same two scripts plus their `GOV_FALSIFY=1` control — where a *passing*
control is a FAILURE, because an audit that cannot detect its own injected defect has measured nothing
— so the only thing added is finding out before you tag instead of after. Two checkers for one
question would drift apart, which is the class this whole file documents.

**It does not dirty the tree it is clearing.** You run it immediately before `git commit && git tag`,
and the suite executes `probes/governance_sufficiency_probe.py`, which regenerates
`probes/governance_sufficiency_bytes.json` with fresh record ids and keys — 45 lines of churn in a
tracked file, one `git add -A` from being committed into the release. The checklist snapshots every
probe receipt as **bytes** at the start of the run and restores whatever changed, reporting which ones
rather than repairing them silently. (Bytes because that receipt is not valid UTF-8 — 0x97 at offset
2321 — and because text mode on Windows rewrites LF as CRLF, which is the permanently-dirty-file bug
`tools/mutation_check.py` documents.)

`tests/test_release_check_has_teeth.py` exercises each check in **both** directions on a copied tree:
it passes on a consistent copy and fails on a copy with exactly one thing wrong. A check verified only
in the passing direction cannot tell "the tree is clean" from "the reader stopped matching", which is
precisely what produced the `CITATION.cff` drift below. One of them is a coupling test: it runs the
**real pinner** against a copy, sees which files it changed, and requires every one of them to be a
carrier the checklist reads — so a manifest added to the pinner tomorrow cannot go unchecked.

**The fourth instance of the same class, found while building that checklist: `CITATION.cff`.** It
carries a version, nothing pinned it and no test asserted it, so it read `1.1.0` while the package went
from 1.2.0 to 1.88.1 — measured over the git history, **111 distinct released versions disagreed with
it**. It is not an internal manifest: Zenodo mints the DOI record from this file, so for that whole
window every citation of this software named a version that had not existed since the second week. It
was hand-corrected at 1.88.1, which fixed the instance and left the class exactly where it was. The
pinner now writes it, with a control that it REFUSES when the `version:` key goes missing rather than
quietly pinning nothing.

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

## The release itself is the artifact

Adoption here is release-driven, not capability-driven. From an analysis of this package's PyPI
download history: **555 downloads/day on release days against 9 on quiet days, r=0.977.** Provenance,
because it is the premise of everything below and it is the one number here that no artifact in this
repository recomputes: it was measured off the public PyPI download series (`pypistats.org/packages/
inspeximus`), not by anything runnable from a clone, and the window and n are not recorded with it. It
is reproduced as reported. Re-derive it before it carries any decision heavier than "write the notes
for a reader" — which is all it is doing here, and which is worth doing regardless of the exact
coefficient.

Shipping a capability moves nothing until there is a release to announce it, and the release is the
one moment a stranger reads us.

What they read is currently written for us. The changelog is a record of what we found and repaired,
in the order we found it — the right document for us and the wrong one for someone deciding whether to
install this. `tools/release_notes.py` fills `docs/RELEASE_NOTES_TEMPLATE.md` from the changelog entry
and adds the four frames it lacks: **who should upgrade, what changed, what breaks, and the one
command to try it.**

It **derives**, and refuses to invent. `Who should upgrade` comes from our `UPGRADE IF YOU USE ...`
heading convention; when the heading does not say, it emits a `TODO(...)` and the gate rejects the
notes rather than writing "everyone should upgrade", which nobody measured. `What breaks` reports only
the lines the entry marks `BEHAVIOUR CHANGE` / `BREAKING`, and when there are none it says exactly
that — a statement about the entry, which a reader can check against the source, not a compatibility
promise about the code, which they cannot.

Two rules are enforced as code, so they are not a matter of taste:

- **No claim a reader cannot check.** A deterministic lint rejects "revolutionary", "blazing",
  "seamless", "industry-leading" and the rest, and — the one that matters for us — the comparative
  negative: "no other library", "competitors can't". We are allowed to say what inspeximus *does*
  (deterministically, zero-LLM, in a single file), because each of those is a property someone can
  verify; we are not allowed to say what other people cannot do, which we have not measured and could
  not maintain. Measured across all 107 version entries in `CHANGELOG.md`: **zero** false positives,
  with a positive control in the tests proving it still fires.
- **The example must run, and its output is asserted.** The Python block ends in a `# -> ` line
  stating its expected stdout; the gate executes the block against this tree and compares. A "try it
  in 30 seconds" example that no longer works is worse than no example, and this is the first code a
  new reader runs.

## Never

- Never anchor a test to a line number. The recorded mutation-survivor lines were stale within a day:
  what was `core.py:3662` became a comment.
- Never retag a published version to make a red CI run green. 1.68.0's tag stays red; the fix landed on
  `main` and proved itself on the next release.
- Never `git checkout --` / anything that rewrites the working tree with an uncommitted fix in it. That
  destroyed a fix once and it had to be written twice.
