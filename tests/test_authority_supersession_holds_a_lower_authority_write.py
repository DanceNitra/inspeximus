"""`supersession="authority"` (3.2.0): a keyed write below the incumbent's authority is retired on arrival.

Each test names the property it holds and, where the rule has a known limit, asserts the limit
rather than hiding it: `test_lost_update_0001_is_wrong_under_both_policies` is the MemTX case where
last-write-wins serves 47, authority serves 50 and the label says 48. If that test ever passes the
"right" answer, the rule changed and the docstring in `_supersede_by_key` is stale.

The default is measured first, as the control: a store built without the flag behaves as 3.1.0.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.core import _declared_authority, _resolve_supersession


def _mk(**kw):
    p = os.path.join(tempfile.mkdtemp(), "s.json")
    return p, Inspeximus(path=p, **kw)


def _w(ix, key, value, authority=None, doc="d", **kw):
    src = {"doc": doc} if authority is None else {"doc": doc, "authority": authority}
    return ix.remember(f"{key} is {value}", key=key, object=str(value), source=src, **kw)


def test_CONTROL_the_default_is_last_write_wins_and_never_emits_the_authority_policy():
    p, ix = _mk()
    assert ix.supersession == "lww"
    _w(ix, "k", 50, authority=1.0)
    b = _w(ix, "k", 48, authority=0.8)
    assert ix.current("k")["id"] == b and ix.last_write["policy"] is None
    policies = {r.get("meta", {}).get("superseded_by_policy") for r in ix.items}
    assert "keyed_authority" not in policies and "keyed_lww" in policies
    assert not any("rejected_authority" in r.get("meta", {}) for r in ix.items)


def test_a_lower_authority_write_is_held_and_the_verdict_is_on_the_record_and_on_last_write():
    p, ix = _mk(supersession="authority")
    a = _w(ix, "k", 50, authority=1.0)
    b = _w(ix, "k", 48, authority=0.8)
    assert ix.current("k")["id"] == a
    rec = next(r for r in ix.items if r["id"] == b)
    assert rec["status"] == "superseded"
    assert rec["meta"]["superseded_by_policy"] == "keyed_authority"
    assert rec["meta"]["rejected_authority"] == 0.8 and rec["meta"]["retained_authority"] == 1.0
    assert rec["meta"]["superseded_by_toggle"] == a
    lw = ix.last_write
    assert lw["blocked"] and lw["policy"] == "keyed_authority" and lw["current_id"] == a
    assert lw["rejected_authority"] == 0.8 and lw["retained_authority"] == 1.0


def test_equal_authority_goes_to_the_later_write_and_higher_authority_wins():
    p, ix = _mk(supersession="authority")
    _w(ix, "k", 50, authority=0.8)
    b = _w(ix, "k", 48, authority=0.8)
    assert ix.current("k")["id"] == b and ix.last_write["policy"] is None
    c = _w(ix, "k", 49, authority=1.0)
    assert ix.current("k")["id"] == c


def test_the_laundering_rule_a_summary_carries_its_weakest_parent():
    p, ix = _mk(supersession="authority")
    a = _w(ix, "k", 50, authority=1.0)
    rumour = ix.remember("a rumour says 40", source={"doc": "rumour", "authority": 0.3})
    # a 1.0 summary of a 0.3 rumour is 0.3
    s1 = ix.remember("summary: 40", key="k", object="40", source={"doc": "s", "authority": 1.0},
                     derived_from=[rumour])
    assert ix.current("k")["id"] == a and ix.last_write["rejected_authority"] == 0.3
    # a summary that DECLARES NOTHING but derives from the rumour is 0.3 as well
    s2 = ix.remember("summary again: 41", key="k", object="41", source={"doc": "s2"}, derived_from=[rumour])
    assert ix.current("k")["id"] == a and ix.last_write["rejected_authority"] == 0.3
    # two levels down still counts
    mid = ix.remember("digest of the rumour", source={"doc": "mid", "authority": 0.9}, derived_from=[rumour])
    s3 = ix.remember("digest: 42", key="k", object="42", source={"doc": "s3", "authority": 1.0}, derived_from=[mid])
    assert ix.current("k")["id"] == a and ix.last_write["rejected_authority"] == 0.3


def test_the_echo_guard_runs_before_authority_and_a_retired_key_accepts_the_next_write():
    p, ix = _mk(supersession="authority")
    _w(ix, "k", 50, authority=0.5)
    b = _w(ix, "k", 48, authority=0.5)                       # 50 is now a superseded object
    e = _w(ix, "k", 50, authority=1.0)                        # an echo of 50, at higher authority
    assert ix.current("k")["id"] == b
    assert ix.last_write["policy"] == "echo_guard", "the echo guard, not authority, must answer first"
    ix.retire("k", "ended")
    assert ix.current("k") is None
    n = _w(ix, "k", 1, authority=0.1)                         # nothing active to compare against
    assert ix.current("k")["id"] == n and ix.last_write["policy"] is None


def test_authority_decides_only_when_both_sides_declare_it():
    p, ix = _mk(supersession="authority")
    legacy = ix.remember("k is L", key="k", object="L")            # no source at all
    d = _w(ix, "k", "D", authority=0.5)
    assert ix.current("k")["id"] == d, "a declared write lands on a legacy incumbent"
    u = ix.remember("k is U", key="k", object="U", source={"doc": "x"})   # declares nothing
    assert ix.current("k")["id"] == u, "an undeclared write lands on a declared incumbent"
    # and the pair of declared values still compares
    _w(ix, "k", "H", authority=1.0)
    lo = _w(ix, "k", "lo", authority=0.2)
    assert ix.current("k")["object"] == "H"


def test_turning_the_policy_on_over_an_existing_store_changes_nothing_until_authorities_arrive():
    p, ix = _mk()
    _w(ix, "k", 50)
    _w(ix, "k", 48)
    ix.flush()
    again = Inspeximus(path=p, supersession="authority")
    assert again.current("k")["object"] == "48"
    c = _w(again, "k", 47, authority=0.1)
    assert again.current("k")["id"] == c


def test_a_garbage_authority_is_refused_under_the_policy_and_inert_under_lww():
    p, ix = _mk(supersession="authority")
    with pytest.raises(ValueError, match="finite number"):
        ix.remember("k is x", key="k", object="x", source={"doc": "d", "authority": "high"})
    with pytest.raises(ValueError, match="finite number"):
        ix.remember("k is x", key="k", object="x", source={"doc": "d", "authority": float("nan")})
    assert ix.items == []
    q, lww = _mk()
    lww.remember("k is x", key="k", object="x", source={"doc": "d", "authority": "high"})
    assert lww.items[0]["source"]["authority"] == "high"


def test_declared_authority_is_clamped_and_none_when_absent():
    assert _declared_authority({"doc": "d", "authority": 7}) == 1.0
    assert _declared_authority({"doc": "d", "authority": -1}) == 0.0
    assert _declared_authority({"doc": "d", "authority": "0.25"}) == 0.25
    assert _declared_authority({"doc": "d"}) is None
    assert _declared_authority({"doc": "d", "authority": True}) is None
    assert _declared_authority("d") is None and _declared_authority(None) is None


def test_reaffirm_bypasses_the_rule():
    p, ix = _mk(supersession="authority")
    _w(ix, "k", 50, authority=1.0)
    b = _w(ix, "k", 48, authority=0.2, reaffirm=True)
    assert ix.current("k")["id"] == b


def test_the_rule_is_tenant_scoped_and_reaches_agent_views():
    p, ix = _mk(supersession="authority")
    acme, globex = ix.for_tenant("acme"), ix.for_tenant("globex")
    a = _w(acme, "k", 50, authority=1.0)
    g = _w(globex, "k", 48, authority=0.2)
    assert globex.current("k")["id"] == g, "another tenant's 1.0 is not this tenant's incumbent"
    held = _w(acme, "k", 47, authority=0.2)
    assert acme.current("k")["id"] == a and acme.last_write["policy"] == "keyed_authority"
    alice = ix.as_agent("alice")
    x = _w(alice, "roadmap", "v1", authority=0.9)
    _w(alice, "roadmap", "v2", authority=0.4)
    assert alice.current("roadmap")["id"] == x and alice.supersession == "authority"


def test_lost_update_0001_is_wrong_under_both_policies():
    """MemTX lost_update_0001, verbatim shape: system seeds 50 at 1.0, two agents at 0.8 write 48 then
    47, and the label keeps 48. Neither policy serves 48. This test pins the limit the docstring
    states; if either arm starts serving 48, update the docstring before celebrating."""
    for policy, served in (("lww", "47"), ("authority", "50")):
        p, ix = _mk(supersession=policy)
        _w(ix, "inv::qty", 50, authority=1.0, doc="system")
        _w(ix, "inv::qty", 48, authority=0.8, doc="agent_B")
        _w(ix, "inv::qty", 47, authority=0.8, doc="agent_A")
        assert ix.current("inv::qty")["object"] == served, policy
        assert ix.current("inv::qty")["object"] != "48"


def test_the_held_write_survives_a_reopen_the_l1_and_the_receipt_chain():
    p, ix = _mk(supersession="authority", receipts=True)
    a = _w(ix, "k", 50, authority=1.0)
    assert ix.current("k")["id"] == a                                  # L1 warm
    b = _w(ix, "k", 48, authority=0.8)
    assert ix.current("k")["id"] == a, "the L1 must not serve the held value"
    again = Inspeximus(path=p, supersession="authority", receipts=True)
    assert again.current("k")["id"] == a
    rec = next(r for r in again.items if r["id"] == b)
    assert rec["meta"]["superseded_by_policy"] == "keyed_authority"
    assert again.verify_writes() == (True, [])


def test_the_policy_resolves_explicit_then_env_then_lww(monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_SUPERSESSION", raising=False)
    assert _resolve_supersession() == "lww"
    monkeypatch.setenv("INSPEXIMUS_SUPERSESSION", "authority")
    assert _resolve_supersession() == "authority"
    assert _resolve_supersession("lww") == "lww"
    p, ix = _mk()
    assert ix.supersession == "authority"
    with pytest.raises(ValueError, match="supersession must be one of"):
        _resolve_supersession("newest")
