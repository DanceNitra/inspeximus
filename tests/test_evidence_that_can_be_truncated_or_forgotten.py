"""Two artifacts whose entire job is to be evidence, defeated by DELETING part of them.

Not by forging anything — by removing. Both were found by an adversarial pass on 2026-08-15 and both
share a shape: a check that RECOMPUTES its verdict from evidence it never counted.

  * `DeletionManifest.verify()` recomputes `complete` and `residual_targets` from the entries. That
    closed an earlier "signed lie" hole and opened a quieter one: A PREFIX OF A HASH CHAIN IS A VALID
    HASH CHAIN. Drop the trailing entry — the one recording the target where the subject's data is
    still present — and the recomputation runs over evidence that says everything was clean.
    Measured on a SIGNED, key-pinned manifest: `complete: True, residual_targets: []`, verify ->
    (True, []).

  * `Witness.__init__` swallowed a broken state file and continued with no memory. The module's own
    docstring says that memory is the guarantee: "without it, an operator could restart the witness
    and get a fork past it." Write garbage into one JSON file — or delete it — and the witness
    co-signed a rollback it had just refused.
"""
from __future__ import annotations

import copy
import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.deletion_manifest import DeletionManifest, ErasureTarget
from inspeximus.witness_pool import Witness


class _Target(ErasureTarget):
    def __init__(self, name, clean):
        self.name, self._clean = name, clean

    def erase(self, subj):
        return {"erased": 1}

    def still_recoverable(self, subj, vals):
        return not self._clean


def _manifest():
    dm = DeletionManifest()
    dm.register(_Target("inspeximus-store", True))
    dm.register(_Target("qdrant-index", False))          # the subject's data is STILL THERE
    return dm, dm.execute("alice", ["alice@example.com"], request_id="DSAR-1",
                          basis="art17", authorized_by="dpo@example")


def _truncate(m):
    t = copy.deepcopy(m)
    t["entries"] = t["entries"][:1]
    t["complete"] = all(x.get("verified_absent") for x in t["entries"])
    t["residual_targets"] = []
    return t


# ───────────────────────────────────────────────────── deletion manifest
def test_an_honest_manifest_reports_the_residue():
    dm, m = _manifest()
    assert m["complete"] is False and m["residual_targets"] == ["qdrant-index"]
    assert dm.verify(m) == (True, [])


def test_a_truncated_manifest_cannot_report_itself_complete():
    dm, m = _manifest()
    t = _truncate(m)
    assert t["complete"] is True and t["residual_targets"] == [], \
        "the fixture no longer builds the lie this test exists to catch"
    ok, problems = dm.verify(t)
    assert not ok and any("TRUNCATED" in p for p in problems), problems


def test_control_trimming_the_header_to_match_breaks_the_chain():
    """THE control that makes the fix sufficient rather than merely another recomputation. `targets`
    is inside the header hash, so an attacker cannot trim it to match the shortened entry list."""
    dm, m = _manifest()
    t = _truncate(m)
    t["targets"] = ["inspeximus-store"]
    ok, problems = dm.verify(t)
    assert not ok and any("chain link" in p for p in problems), problems


@pytest.mark.parametrize("mut,needle", [
    (lambda t: t["entries"].append(copy.deepcopy(t["entries"][0])), "more than once"),
    (lambda t: t.__setitem__("targets", t["targets"] + ["a-target-never-audited"]), "NO entry"),
])
def test_the_entry_set_must_match_the_declared_targets_both_ways(mut, needle):
    dm, m = _manifest()
    t = copy.deepcopy(m)
    mut(t)
    ok, problems = dm.verify(t)
    assert not ok and any(needle in p for p in problems), problems


# ───────────────────────────────────────────────────── witness fork-memory
def _heads():
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    for i in range(3):
        ix.remember(f"record {i}", key=f"k{i}")
    honest = ix.anchor()
    short = Inspeximus(path=os.path.join(d, "s2.json"), receipts=True)
    short.remember("one", key="k0")
    return d, honest, short.anchor()


def test_control_a_witness_with_its_memory_refuses_the_rollback():
    """POSITIVE CONTROL first: if this stops refusing, every test below passes for the wrong reason."""
    d, honest, rollback = _heads()
    sp = os.path.join(d, "w.json")
    w = Witness(state_path=sp)
    w.cosign("store-1", honest)
    with pytest.raises(ValueError, match="rolled back"):
        Witness(w._secret, state_path=sp).cosign("store-1", rollback)


def test_a_corrupt_fork_memory_stops_the_witness_rather_than_erasing_it():
    d, honest, _rollback = _heads()
    sp = os.path.join(d, "w.json")
    w = Witness(state_path=sp)
    w.cosign("store-1", honest)
    open(sp, "w", encoding="utf-8").write("{ this is not json")
    with pytest.raises(ValueError, match="could not be read"):
        Witness(w._secret, state_path=sp)


def test_strict_closes_the_deletion_case_that_refusing_a_corrupt_file_cannot():
    """A DELETED state file and a genuine first run are the same bytes, so refusing corruption is
    only half of it. Under strict, first contact is an explicit act and silence becomes evidence."""
    d, honest, rollback = _heads()
    sp = os.path.join(d, "w.json")
    w = Witness(state_path=sp, strict=True)
    w.bootstrap("store-1")
    w.cosign("store-1", honest)
    os.unlink(sp)
    with pytest.raises(ValueError, match="no record of store"):
        Witness(w._secret, state_path=sp, strict=True).cosign("store-1", rollback)


def test_strict_still_allows_a_genuine_first_store_when_declared():
    """The must-not-brick control: a mode that refuses everything is not a security feature."""
    d, honest, _ = _heads()
    w = Witness(state_path=os.path.join(d, "w2.json"), strict=True)
    w.bootstrap("brand-new")
    assert w.cosign("brand-new", honest)


def test_the_default_is_unchanged():
    """Default OFF, deliberately: a witness pool that refuses every new store on upgrade is a worse
    outcome than the attack it prevents."""
    d, honest, _ = _heads()
    assert Witness(state_path=os.path.join(d, "w3.json")).cosign("any-store", honest)
