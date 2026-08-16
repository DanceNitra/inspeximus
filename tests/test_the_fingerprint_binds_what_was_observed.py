"""Refetchability and observation binding are two claims, and we were reporting one as both.

WHERE THIS COMES FROM. anthropics/claude-code#34556. safal207 found that OmniMemory recorded
`git rev-parse HEAD:<path>` -- the COMMITTED blob -- while an agent mid-session reads the WORKING
TREE. The locator resolved perfectly, to the wrong observation. He then named the split and asked
for it as two independently measurable numbers:

    refetchability      can the source be resolved again?
    observation binding is the fingerprint of the exact bytes the agent actually observed?

WE NEVER HAD THE GIT VERSION -- we hash the file on disk, which IS the working tree -- and that made
it easy to conclude the finding did not apply to us. It did, one layer in: we hashed at WRITE time,
and an agent reads, reasons, and only then calls remember(). Measured 2026-08-16 before the fix: read
`value='A'`, let the file become `'B'`, write a memory claiming A, and the store recorded B's digest
and reported FRESH. A wrong-but-resolvable fingerprint is worse than none -- false confidence rather
than an absence.

THE FOUR STATES, and the honest limit is the second one:

    no observed claim, unchanged        FRESH             ok       bound 0.0
    no observed claim, moved first      FRESH             ok       bound 0.0   <- undetectable, and
                                                                                  no longer implied
    observed claim, unchanged           FRESH             ok       bound 1.0
    observed claim, moved before write  UNBOUND_CAPTURE   NOT ok   bound 1.0

Row 2 cannot be closed in code: without the caller telling us what it read, the information is not
ours to have. What we no longer do is let refetch coverage stand in for it.

TWICE I WAS WRONG ON THE WAY HERE, and both are why the controls below exist. First I looked for the
fingerprint in `source` and found nothing -- it lives in reserved meta precisely so the writer cannot
forge it -- and nearly reported a gap we do not have. Then I nearly filed the moved-capture case as
DRIFTED, which says the bytes changed AFTERWARDS; here they changed BEFORE, and the remedy differs
(re-read and re-capture, not re-derive).
"""
from __future__ import annotations

import hashlib
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _capture(supply_observed: bool, move_before_write: bool):
    d = tempfile.mkdtemp()
    f = os.path.join(d, "cfg.py")
    open(f, "w").write("value='A'")
    observed = _sha(open(f, "rb").read())              # what the agent actually reads
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    if move_before_write:
        open(f, "w").write("value='B'")
    src = {"doc": f}
    if supply_observed:
        src["observed_sha256"] = observed
    ix.remember("the config sets value to A", key="cfg", object="A", source=src)
    return ix, f, observed


@pytest.mark.parametrize("supply,move,expect,ok,bound", [
    (False, False, "FRESH", True, 0.0),
    (False, True, "FRESH", True, 0.0),          # the honest limit: not ours to know
    (True, False, "FRESH", True, 1.0),
    (True, True, "UNBOUND_CAPTURE", False, 1.0),
])
def test_the_four_states(supply, move, expect, ok, bound):
    ix, _f, _o = _capture(supply, move)
    rep = ix.check_sources()
    assert rep["counts"].get(expect) == 1, rep["counts"]
    assert rep["ok"] is ok
    assert rep["coverage"]["observation_binding_coverage"] == bound


def test_a_caller_that_says_what_it_read_is_bound_to_it():
    """The fix, at the mechanism. The recorded fingerprint must be the OBSERVED bytes, not the file
    as it stood when remember() happened to run."""
    ix, _f, observed = _capture(supply_observed=True, move_before_write=True)
    assert (ix.items[0]["meta"] or {}).get("source_sha256") == observed


def test_the_moved_capture_is_not_filed_as_drift():
    """Neither of the existing verdicts fits, and using one would be wrong in a way that changes what
    an operator does. DRIFTED says the bytes changed AFTER capture -- re-derive. Here they changed
    BEFORE -- re-read and re-capture, because the bytes that produced this memory are gone."""
    ix, _f, _o = _capture(supply_observed=True, move_before_write=True)
    c = ix.check_sources()["counts"]
    assert c.get("UNBOUND_CAPTURE") == 1 and not c.get("DRIFTED") and not c.get("FRESH")


def test_a_writer_cannot_declare_its_own_memory_observation_bound():
    """The reserved keyspace, extended to the new keys. A caller that could set `observation_bound`
    or `source_sha256` through `meta` could certify freshness for content it changed -- the trust-tier
    hole this keyspace exists to close."""
    d = tempfile.mkdtemp()
    f = os.path.join(d, "cfg.py")
    open(f, "w").write("value='A'")
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("forged", key="k", source={"doc": f},
                meta={"observation_bound": True, "source_sha256": "00" * 32,
                      "observed_sha256": "11" * 32})
    m = ix.items[0]["meta"] or {}
    assert m.get("source_sha256") != "00" * 32, "a writer forged its own fingerprint"
    assert m.get("observation_bound") is False, "a writer declared itself observation-bound"


# ─────────────────────────────────────────────── the controls that stop this flattering us
def test_control_an_honest_capture_is_fresh():
    """If this fails, the fingerprint is broken generally and every test above is about the wrong
    thing."""
    ix, _f, _o = _capture(supply_observed=False, move_before_write=False)
    assert ix.check_sources()["counts"].get("FRESH") == 1


def test_control_a_real_later_edit_is_still_drift():
    """The existing behaviour must survive: a source that changes AFTER capture is DRIFTED, and the
    new verdict must not have swallowed it."""
    ix, f, _o = _capture(supply_observed=True, move_before_write=False)
    open(f, "w").write("value='CHANGED LATER'")
    c = ix.check_sources()["counts"]
    assert c.get("DRIFTED") == 1 and not c.get("UNBOUND_CAPTURE")


def test_control_a_store_with_nothing_checkable_still_refuses_to_read_clean():
    """inspeximus already refuses to let "0 drifted over 0 checked" look like a clean store. Adding a
    fifth counter must not have given a vacuous check a way to pass."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"))
    ix.remember("x", key="k", source={"doc": "agent:scholar"})   # a writer, not a document
    rep = ix.check_sources()
    assert rep["ok"] is False and rep["counts"]["UNCHECKABLE"] == 1


def test_the_two_coverages_can_disagree():
    """THE test that keeps the split honest. One number being 1.0 while the other is 0.0 is the whole
    point -- if they can only move together, they are one metric wearing two names."""
    d = tempfile.mkdtemp()
    f = os.path.join(d, "cfg.py")
    open(f, "w").write("value='A'")
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("no claim about what was read", key="k", source={"doc": f})
    cov = ix.check_sources()["coverage"]
    assert cov["refetch_verification_coverage"] == 1.0
    assert cov["observation_binding_coverage"] == 0.0
