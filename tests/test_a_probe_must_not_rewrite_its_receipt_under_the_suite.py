"""The suite runs probes; a probe must not replace the committed measurement while it does.

A probe's `.result.json` is the number someone cites. The suite executes probes as smoke tests, in
parallel, on a machine already running several thousand tests, so a receipt written during a suite
run describes a different machine. The first sign is a working tree that looks casually edited.

THREE SEPARATE COMMITS ADDED THE SAME ONE-LINE GUARD BY HAND: 07b281c to two probes, then the lock
probe, then identity_gate_supersession after CI reported `running an example dirtied tracked
files`. A rule that lives in every caller gets missed in the next one.

MEASURED 2026-09-11, one run of tests/test_probes_cited_by_docs.py on a clean tree: NINE tracked
receipts went dirty. That is the real exposure, not the 28 that merely lack the guard -- the other
19 are never executed by the suite. The number matters because a list this test pins has to be the
measured one, or it becomes a wish.

WHAT THIS TEST DOES, and what it deliberately does not. It does not assert the nine are fixed; they
are not, and saying otherwise in a green test is the failure this file is about. It pins the list,
so a tenth probe joining it fails here with its name, and it asserts the guard is real by checking
the ones that DO have it actually honour it.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Probes measured to rewrite a TRACKED receipt when the suite runs them. Shrinking this list is
#: the work; growing it without a line here is the regression.
#:
#: MEASURED 2026-09-11, twice, one serial run of tests/test_probes_cited_by_docs.py each time on a
#: clean tree: NINE receipts dirty before, ZERO after. The nine are now empty because they were
#: converted, not because the list was edited.
KNOWN_DIRTYING: set[str] = set()

#: Every probe that owns a tracked receipt and is executed by the suite. Each must route its write
#: through probes/_receipt.py or carry the inline guard; the static test below names whichever one
#: stops doing so. An empty KNOWN_DIRTYING measures nothing on its own, so this is what replaces it:
#: a property checked cheaply on every run, rather than a list that is true only until someone adds
#: a probe.
MUST_NOT_WRITE_UNDER_THE_SUITE = (
    "a_record_retired_by_consolidate_is_still_valid_to_an_as_of_query.py",
    "a_write_that_touches_one_row_instead_of_the_whole_store.py",
    "dogfood_cross_session.py",
    "erasure_elicitation.py",
    "governance_sufficiency_probe.py",
    "identity_gate_supersession_probe.py",
    "integrity_bench_determinism.py",
    "integrity_bench_store_resolves.py",
    "one_write_two_formats_across_store_sizes.py",
    "recall_iterative_surface_multihop.py",
    "reinforce_accuracy_ablation.py",
    "twelve_writers_and_the_one_that_stopped_writing.py",
    "what_the_lock_is_actually_holding_back.py",
)


def _tracked_probe_receipts():
    # encoding= is not optional here, and this repo has a test that says so: text=True without it
    # decodes with the machine's locale codec, so a child printing UTF-8 returns stdout=None and the
    # assertion below reports a defect it never measured. That test caught this file in CI.
    out = subprocess.run(["git", "ls-files", "probes/*.json"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    return {ln.strip() for ln in (out or "").splitlines() if ln.strip()}


def test_the_known_dirtying_receipts_are_all_real_tracked_files():
    """A pinned list of names that no longer exist would pass forever while measuring nothing."""
    tracked = _tracked_probe_receipts()
    assert tracked, "git ls-files returned nothing, so this test cannot see its subject"
    missing = sorted(KNOWN_DIRTYING - tracked)
    assert not missing, (
        "these are pinned as dirtying receipts but are not tracked files any more, so the pin is "
        "measuring nothing: %s" % missing)


def test_the_helper_is_not_counted_as_one_of_its_own_callers():
    """THE PREVIOUS VERSION OF THIS TEST COUNTED probes/_receipt.py.

    It globbed probes/*.py for the string PYTEST_CURRENT_TEST and required at least three hits. The
    helper's own docstring and its `suppressed()` both contain that string, so the helper matched
    itself. On 2026-09-11 the helper had ZERO call sites and nine probes were rewriting tracked
    receipts, and this test was green throughout: a grep whose subject includes the thing it is
    grepping for cannot report an adoption of nothing.
    """
    helper = ROOT / "probes" / "_receipt.py"
    assert helper.exists(), "the helper this class depends on is gone"
    assert "PYTEST_CURRENT_TEST" in helper.read_text(encoding="utf-8", errors="replace"), (
        "the helper no longer mentions the variable, so a string audit of probes/ would now miss it "
        "rather than double-count it -- either way, count callers, not matches")


def test_every_probe_that_must_not_write_routes_through_the_helper_or_guards_itself():
    """The property, checked on every run, instead of a list that is true until someone adds a probe.

    Two spellings are accepted because both are real: a call into probes/_receipt.py, which is where
    the rule now lives, and the inline `if os.environ.get("PYTEST_CURRENT_TEST")` that four probes
    carried before the helper existed. What is NOT accepted is neither.
    """
    unprotected = []
    for name in MUST_NOT_WRITE_UNDER_THE_SUITE:
        p = ROOT / "probes" / name
        if not p.exists():
            unprotected.append("%s (missing: the pin is naming a file that is gone)" % name)
            continue
        s = p.read_text(encoding="utf-8", errors="replace")
        routed = "write_json(" in s or "write_receipt(" in s
        inline = 'os.environ.get("PYTEST_CURRENT_TEST")' in s
        if not (routed or inline):
            unprotected.append(name)
    assert not unprotected, (
        "these probes own a tracked receipt, are executed by the suite, and neither route their "
        "write through probes/_receipt.py nor guard it inline: %s" % unprotected)


def test_the_helper_actually_suppresses_and_leaves_the_file_alone():
    """The guard is one boolean. This is the mutation that proves it load-bearing rather than decorative.

    Measured by hand on 2026-09-11 before it was written down: with `suppressed()` forced to False,
    running governance_sufficiency_probe.py under PYTEST_CURRENT_TEST changed its tracked receipt;
    with the guard in place, the same run left it byte-identical.
    """
    sys.path.insert(0, str(ROOT / "probes"))
    try:
        import _receipt
    finally:
        sys.path.pop(0)

    target = ROOT / "probes" / "_guard_probe_scratch.json"
    try:
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        assert _receipt.write_json(str(target), {"n": 1}, indent=1) is not None, \
            "a person running a probe must still get a receipt"
        first = target.read_bytes()

        os.environ["PYTEST_CURRENT_TEST"] = "guard::check"
        assert _receipt.write_json(str(target), {"n": 2}, indent=1) is None, \
            "under the suite the write must be refused"
        assert target.read_bytes() == first, \
            "the write was reported as refused and the file changed anyway"
    finally:
        os.environ["PYTEST_CURRENT_TEST"] = "restored::by_finally"
        if target.exists():
            target.unlink()


def test_the_suite_does_not_suppress_a_file_the_caller_asked_for(tmp_path):
    """THE ARM THE FIRST VERSION OF THE GUARD BROKE, and that nothing here was measuring.

    `write_json` suppressed on `suppressed()` alone, so a probe invoked with `--out <tmp>/r.json`
    under the suite wrote nothing, and tests/test_dogfood_cross_session.py failed in CI on a missing
    file. Every local check had looked at tracked receipts, which is what the guard protects: the
    arm meant to stay untouched had no test at all.

    The rule is about COMMITTED receipts. A path the caller named is the caller's business.
    """
    sys.path.insert(0, str(ROOT / "probes"))
    try:
        import _receipt
    finally:
        sys.path.pop(0)

    os.environ["PYTEST_CURRENT_TEST"] = "guard::check"
    mine = tmp_path / "r.json"
    assert _receipt.write_json(str(mine), {"n": 1}, indent=1) is not None, \
        "a path the caller named must still be written under the suite"
    assert mine.exists(), "write_json reported success and wrote nothing"

    assert not _receipt.is_committed_receipt(str(mine)), \
        "a temp directory is not a committed receipt"
    assert _receipt.is_committed_receipt(str(ROOT / "probes" / "anything.json")), \
        "a path under probes/ is a committed receipt and must be recognised as one"


@pytest.mark.parametrize("probe", ["what_the_lock_is_actually_holding_back.py",
                                   "identity_gate_supersession_probe.py"])
def test_a_guarded_probe_really_honours_it(probe, monkeypatch):
    """The guard exists in the text. This runs the thing and requires the receipt to be untouched,
    because a guard that is present and unreachable reads identically in a grep."""
    src = ROOT / "probes" / probe
    if not src.exists():
        pytest.skip("%s is not in this tree" % probe)
    receipts = [r for r in _tracked_probe_receipts() if r.startswith("probes/" + src.stem.split("_probe")[0])]
    if not receipts:
        pytest.skip("%s has no tracked receipt to defend" % probe)
    before = {r: (ROOT / r).read_bytes() for r in receipts if (ROOT / r).exists()}
    if not before:
        pytest.skip("no receipt on disk")
    env = dict(os.environ, PYTEST_CURRENT_TEST="guard::check")
    # bytes on purpose: nothing here reads the child's stdout, and not decoding it cannot be wrong.
    subprocess.run([sys.executable, str(src)], cwd=ROOT, env=env,
                   capture_output=True, timeout=900)
    after = {r: (ROOT / r).read_bytes() for r in before}
    changed = [r for r in before if before[r] != after[r]]
    assert not changed, (
        "%s carries the guard and rewrote its receipt anyway: %s" % (probe, changed))
