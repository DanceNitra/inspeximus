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

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Probes measured to rewrite a TRACKED receipt when the suite runs them. Shrinking this list is
#: the work; growing it without a line here is the regression.
KNOWN_DIRTYING = {
    "probes/a_record_retired_by_consolidate_is_still_valid_to_an_as_of_query.result.json",
    "probes/a_write_that_touches_one_row_instead_of_the_whole_store.result.json",
    "probes/dogfood_cross_session.result.json",
    "probes/erasure_elicitation.result.json",
    "probes/governance_sufficiency_bytes.json",
    "probes/integrity_bench_determinism_result.json",
    "probes/integrity_bench_store_resolves_result.json",
    "probes/recall_iterative_surface_multihop_result.json",
    "probes/reinforce_accuracy_ablation.result.json",
}


def _tracked_probe_receipts():
    out = subprocess.run(["git", "ls-files", "probes/*.json"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def test_the_known_dirtying_receipts_are_all_real_tracked_files():
    """A pinned list of names that no longer exist would pass forever while measuring nothing."""
    tracked = _tracked_probe_receipts()
    assert tracked, "git ls-files returned nothing, so this test cannot see its subject"
    missing = sorted(KNOWN_DIRTYING - tracked)
    assert not missing, (
        "these are pinned as dirtying receipts but are not tracked files any more, so the pin is "
        "measuring nothing: %s" % missing)


def test_the_guard_is_spelled_the_same_way_everywhere():
    """Three probes carry the guard. If a fourth spells it differently, a grep-based audit of this
    class silently under-counts, which is how the third instance went unnoticed."""
    guarded = []
    for p in sorted((ROOT / "probes").glob("*.py")):
        s = p.read_text(encoding="utf-8", errors="replace")
        if "PYTEST_CURRENT_TEST" in s:
            guarded.append(p.name)
    assert len(guarded) >= 3, (
        "expected at least the three probes known to carry the guard, found %s" % guarded)


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
    subprocess.run([os.sys.executable, str(src)], cwd=ROOT, env=env,
                   capture_output=True, timeout=900)
    after = {r: (ROOT / r).read_bytes() for r in before}
    changed = [r for r in before if before[r] != after[r]]
    assert not changed, (
        "%s carries the guard and rewrote its receipt anyway: %s" % (probe, changed))
