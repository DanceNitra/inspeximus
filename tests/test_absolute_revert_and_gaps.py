"""The absolute revert path was dead on arrival, plus the remaining named mutation gaps.

`submit_revert("restore:key=value#nonce")` referenced `tgt` in its ABSOLUTE branch, but that name is bound
only in the RELATIVE branch, which returns before reaching it. So every absolute restore with something to
do -- an existing target that is not already current -- raised `UnboundLocalError`. `restore_now`, the
documented "mint + submit in ONE call" liveness primitive written specifically so a caller cannot wedge
writes into the mint->submit window, crashed with it.

572 tests did not catch it: they exercise the relative path (`revert:key@base#nonce`) only. An entire
documented half of a public API had no test that reached its final statement.

Found while writing tests for unrelated mutation survivors, which is the argument for reading the code
around the target instead of only the target.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _wallet():
    m = Inspeximus(path=_path())
    a = m.remember("wallet is 0xAAA", key="payout::wallet", object="0xAAA")
    b = m.remember("wallet is 0xBBB", key="payout::wallet", object="0xBBB")
    return m, a, b


# ── the absolute path exists at all ─────────────────────────────────────────────────────────────────
def test_an_absolute_restore_lands():
    m, _a, _b = _wallet()
    res = m.submit_revert("restore:payout::wallet=0xAAA#deadbeef")
    assert res["ok"] is True and res["kind"] == "absolute", res
    assert m._route_chain("payout::wallet")[-1] == "0xAAA"


def test_restore_now_lands():
    """The liveness primitive. If this crashes, the store's "maximum bypass is ZERO" guarantee is a
    guarantee about a function that cannot be called."""
    m, _a, _b = _wallet()
    res = m.restore_now("payout::wallet", "0xAAA")
    assert res["ok"] is True, res
    assert m._route_chain("payout::wallet")[-1] == "0xAAA"


def test_the_restore_record_carries_a_real_lineage_edge():
    """The crash was on `derived_from=[tgt["id"]]`. Deleting the edge would also have "fixed" the crash
    while quietly dropping provenance, so assert the edge points at the record that held the value."""
    m, a, _b = _wallet()
    res = m.submit_revert("restore:payout::wallet=0xAAA#cafe01")
    rec = next(r for r in m.items if r["id"] == res["restored"])
    assert rec.get("derived_from") == [a], rec.get("derived_from")


def test_an_absolute_restore_to_the_current_value_is_a_no_op_land():
    m, _a, _b = _wallet()
    res = m.submit_revert("restore:payout::wallet=0xBBB#cafe02")
    assert res["ok"] is True and res["restored"] is None, res


def test_an_absolute_restore_to_a_value_that_never_held_the_key_is_refused():
    """The guard must still refuse, or the fix has widened what lands."""
    m, _a, _b = _wallet()
    res = m.submit_revert("restore:payout::wallet=0xEVIL#cafe03")
    assert res["ok"] is False and res["reason"] == "unknown_target", res


# ── ABA immunity on the id-bound intent ─────────────────────────────────────────────────────────────
def test_an_id_bound_restore_refuses_a_same_value_lookalike():
    """MUTANT: `==` -> `!=` on the id match. ABA: the value is re-asserted and re-killed, so a LOOK-ALIKE
    record now carries the same object. An id-bound intent names one specific instance, and a different
    row with the same value must not satisfy it."""
    m, a, _b = _wallet()
    m.remember("wallet is 0xAAA again", key="payout::wallet", object="0xAAA")   # the look-alike (new id)
    m.remember("wallet is 0xCCC", key="payout::wallet", object="0xCCC")

    good = m.submit_revert(f"restore:payout::wallet=0xAAA@{a}#aba001")
    assert good["ok"] is True and good["id_bound"] is True, good

    bogus = m.submit_revert("restore:payout::wallet=0xAAA@ffffffffff#aba002")
    assert bogus["ok"] is False and bogus["reason"] == "unknown_target", bogus
    assert bogus["id_bound"] is True


# ── the nonce is consumed on evaluation, landed or not ──────────────────────────────────────────────
def test_a_nonce_cannot_be_replayed_even_after_a_refused_intent():
    m, _a, _b = _wallet()
    # the nonce must be hex: the intent regex ends in #([0-9a-f]+), and a non-hex nonce is rejected as
    # malformed BEFORE the branch under test runs -- my first version used "n0nce1" and tested nothing.
    first = m.submit_revert("restore:payout::wallet=0xdead#5eed01")
    assert first["ok"] is False and first["reason"] == "unknown_target"
    again = m.submit_revert("restore:payout::wallet=0xAAA#5eed01")
    assert again["ok"] is False and again["reason"] == "replay_rejected", again


# ── the relative path must not revive an echo-blocked predecessor ───────────────────────────────────
def test_a_relative_revert_refuses_to_revive_an_echo_blocked_record():
    """MUTANT: `or` -> `and` in the predecessor filter, which stops excluding echo-blocked rows unless
    they are ALSO objectless-blocked.

    With the echo guard on, a restatement of an already-superseded value is retired stale-on-arrival and
    marked `echo_blocked`. Reverting must not resurrect that: it is the attacker's re-injection, not the
    store's own history."""
    m = Inspeximus(path=_path())
    m.echo_guard = True
    m.remember("wallet is 0xAAA", key="payout::wallet", object="0xAAA")
    m.remember("wallet is 0xBBB", key="payout::wallet", object="0xBBB")
    m.remember("wallet is 0xAAA", key="payout::wallet", object="0xAAA")     # the echo -> blocked

    blocked = [r for r in m.items if (r.get("meta") or {}).get("echo_blocked")]
    assert blocked, "fixture must actually produce an echo-blocked record, or this proves nothing"

    cur = m._current_active_id("payout::wallet")
    res = m.submit_revert(f"revert:payout::wallet@{cur}#ec4001")
    # Two ways this test passed while testing NOTHING, both fixed here:
    #   * the nonce was "echo01" -- the intent regex ends in #([0-9a-f]+) and 'o' is not hex, so the call
    #     was rejected as malformed before reaching the filter;
    #   * the assertions sat behind `if res["ok"]:`, so that rejection made the body unreachable.
    # It passed against a mutant that demonstrably revives the blocked row. Require the land.
    assert res["ok"] is True, res
    assert res["restored"] not in {r["id"] for r in blocked}
    rec = next(r for r in m.items if r["id"] == res["restored"])
    assert (rec.get("meta") or {}).get("revert_of") not in {r["id"] for r in blocked}, \
        "a revert must not target the echo-blocked row"


# ── a relative revert over a moved base does not land ───────────────────────────────────────────────
def test_a_relative_revert_conflicts_when_the_base_moved():
    m, _a, b = _wallet()
    m.remember("wallet is 0xCCC", key="payout::wallet", object="0xCCC")      # base moves
    res = m.submit_revert(f"revert:payout::wallet@{b}#c0f101")
    assert res["ok"] is False and res["reason"] == "conflict", res
    assert m._route_chain("payout::wallet")[-1] == "0xCCC", "a conflicted revert must change nothing"


@pytest.mark.parametrize("intent", ["nonsense", "revert:key", "restore:key=v", "revert:@#"])
def test_a_malformed_intent_is_refused_without_touching_the_store(intent):
    m, _a, _b = _wallet()
    before = m._route_chain("payout::wallet")
    res = m.submit_revert(intent)
    assert res["ok"] is False, res
    assert m._route_chain("payout::wallet") == before
