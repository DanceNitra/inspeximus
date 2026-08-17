"""The layer BELOW applicability: is the store in a state where the question can be answered?

@Stratogain named it on safal207/Causal-Memory-Layer#289 after we reported collector-silence and
identifier-mismatch as two unrelated cases. The generalisation is theirs -- "liveness is a fact about
the store rather than the record; key agreement is the same sentence with a different noun" -- and so
is the property that makes both dangerous in the same way: **a verdict is produced, so nothing
upstream looks broken.**

The tests below lean on must-fail controls, because a precondition check that cannot fail is the
thing this layer exists to name.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _healthy():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "policy.txt")
    with open(src, "wb") as fh:
        fh.write(b"two approvers")
    ix = Inspeximus(path=os.path.join(d, "s.json"), embed=False, receipts=True)
    ix.remember("deployment needs two approvers", key="policy", object="two",
                source={"doc": src,
                        "observed_sha256": hashlib.sha256(b"two approvers").hexdigest()})
    ix.flush()
    return ix


def _by_id(report):
    return {p["id"]: p for p in report["preconditions"]}


def test_a_healthy_store_satisfies_all_three():
    r = _healthy().admissibility_preconditions()
    assert r["ok"] is True
    assert all(p["holds"] and p["applicable"] for p in r["preconditions"])


# ── 1 · key agreement ────────────────────────────────────────────────────────────────────────
def test_a_key_the_read_path_cannot_resolve_fails_the_precondition():
    """MUST-FAIL CONTROL. @Stratogain's case: the writer stored `session_id[:8]` while the lookup
    compared the full id, so a session could not find its own observations -- and every call still
    returned something, computed from a write-time hash. Simulated from the read side."""
    # THE KEY MUST BE LONGER THAN THE FOLD. The first version of this control used `policy` -- six
    # characters -- so `[:8]` returned it unchanged, nothing broke, and the control passed while
    # measuring nothing. Same defect as everything else on this page: an input that cannot exercise
    # the fault it was written for.
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), embed=False, receipts=True)
    ix.remember("x", key="session-abcdefgh-0001", object="x")
    ix.flush()

    class Truncating(type(ix)):
        def history(self, key, *a, **k):
            return super().history(str(key)[:8], *a, **k)      # queries by a fold of the key

    assert len("session-abcdefgh-0001") > 8, "the fold must be able to change this key"
    broken = Truncating(path=str(ix.path), embed=False, receipts=True)
    p = _by_id(broken.admissibility_preconditions())["key_agreement"]
    assert p["applicable"] and not p["holds"], p
    assert p["failed"], "the check must name the keys that did not resolve"


def test_key_agreement_does_not_fire_on_a_store_whose_keys_all_resolve():
    """The other direction. Without it a check that always reported failure would pass the test
    above, which is how a detector becomes noise inside a week."""
    p = _by_id(_healthy().admissibility_preconditions())["key_agreement"]
    assert p["holds"] and p["failed"] == []


# ── 2 · observation channel ──────────────────────────────────────────────────────────────────
def test_locators_accumulating_with_no_observations_fails():
    """Coverage does not move when the collector dies -- new records get no observation either --
    so the ratio stays reassuring. This asks the question the ratio cannot."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), embed=False, receipts=True)
    ix.remember("a", key="a", object="a", source={"doc": os.path.join(d, "gone.txt")})
    ix.flush()
    p = _by_id(ix.admissibility_preconditions())["observation_channel_alive"]
    assert p["applicable"] and not p["holds"]
    assert p["locators"] >= 1 and p["observations"] == 0


def test_a_store_with_no_locators_at_all_is_NOT_APPLICABLE_rather_than_passing():
    """The distinction the whole file turns on. A store of pure decisions has no locators, so the
    question does not arise -- and a question that did not arise has not been answered. Reporting it
    as holding would be the vacuous pass this layer exists to name."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), embed=False, receipts=True)
    ix.remember("a decision with no source", key="d", object="d")
    ix.flush()
    p = _by_id(ix.admissibility_preconditions())["observation_channel_alive"]
    assert p["applicable"] is False
    assert p["holds"] is False, "inapplicable must not be reported as holding"


# ── 3 · receipts enabled and producing nothing ───────────────────────────────────────────────
def test_receipts_enabled_with_an_empty_chain_fails():
    """FOUND ON OUR OWN STORE while implementing @Stratogain's two: 450 records, receipts enabled,
    chain empty, nothing covered by a write receipt -- and `verify_writes` had been saying so to
    nobody. Same shape as the collector case with a different mechanism."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), embed=False, receipts=True)
    ix.remember("a", key="a", object="a")
    ix.flush()
    ix._receipts = []                                      # the chain our live store actually had
    p = _by_id(ix.admissibility_preconditions())["receipt_chain_covers_records"]
    assert p["applicable"] and not p["holds"]
    assert p["records"] >= 1 and p["chain_entries"] == 0


def test_receipts_disabled_is_not_a_failure():
    """Turning a feature off is a choice; leaving it on while it produces nothing is the defect.
    Without this the check would punish every store that never wanted receipts."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), embed=False, receipts=False)
    ix.remember("a", key="a", object="a")
    ix.flush()
    p = _by_id(ix.admissibility_preconditions())["receipt_chain_covers_records"]
    assert p["applicable"] is False and p["holds"] is True


# ── the report's own honesty ─────────────────────────────────────────────────────────────────
def test_an_empty_store_is_not_ok():
    """No applicable precondition means nothing was established. `ok` must not be True over a store
    that took none of the checks."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), embed=False, receipts=True)
    assert ix.admissibility_preconditions()["ok"] is False


def test_the_report_credits_the_layer_and_states_what_it_does_not_cover():
    r = _healthy().admissibility_preconditions()
    joined = " ".join(r["limits"])
    assert "Stratogain" in joined and "289" in joined
    assert "has NOT passed" in joined
    assert "evaluate_applicability" in joined, "it must say which layer answers the other question"
