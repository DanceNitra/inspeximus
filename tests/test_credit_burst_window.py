"""credit_burst_window — the opt-in collapse of same-polarity credit bursts.

WHY THIS EXISTS. The influence gate is `good_earned > 0 and good >= bad`, so a correct memory with G
earned goods leaves the gate after G+1 failing episodes. An adversary who writes NOTHING, injects no
content and forges no provenance — controlling only the text of queries it may legitimately ask —
shapes queries so a true safety memory is co-recalled on episodes that genuinely FAIL. Every
write-time defense in this library authenticates a WRITER; a credit edge has no author, so all of them
are blind. `credit_requires_warrant` does not help either: a warrant attests the OUTCOME, never the
ATTRIBUTION.

Prior art, and we claim only the transfer: bad-mouthing (Hoffman/Zage/Nita-Rotaru, ACM CSUR 42(1),
2009) and RepTrap (SecureComm 2008) WITHOUT ratings — in every prior formulation the attacker supplies
the negative evidence; here it supplies none.

Four falsifiers, mirroring the guarantee exactly:
  T1  default OFF is unchanged — same counts, no new record keys, unchanged return shape
  T2  the suppression attack costs strictly MORE with the window on
  T3  credit on genuinely SEPARATE occasions still accumulates (else the fix buys suppression
      resistance by making the store unable to learn — that trade was explicitly rejected)
  T4  the attack STILL lands given enough separate occasions. This is COST, not prevention. T4 exists
      so this can never be quietly sold as immunity, and so a future change that silently disables
      demotion altogether fails a test instead of shipping.
"""
import os
import tempfile

import pytest

from inspeximus.core import Inspeximus

SAFETY = "never drop the production database without a signed change ticket and a rollback plan"
BYSTANDER = "rotate the signing keys every ninety days using the offline ceremony"
HONEST_Q = "what is the rule before dropping the production database"
BYSTANDER_Q = "how often do we rotate the signing keys offline ceremony"
# Legitimate to ask, phrased to pull SAFETY into the recalled set, attached to work that genuinely fails.
ATTACK_Q = "drop the production database rollback signed ticket plan for the nonexistent shard"


def _store(window=None, tmp_path=None):
    path = os.path.join(tmp_path or tempfile.mkdtemp(), "store.json")
    m = Inspeximus(path)
    m.credit_burst_window = window
    sid = m.remember(SAFETY, tags=["safety"])
    bid = m.remember(BYSTANDER, tags=["safety"])
    sid = sid["id"] if isinstance(sid, dict) else sid
    bid = bid["id"] if isinstance(bid, dict) else bid
    for i in range(20):
        m.remember(f"runbook entry {i}: restart the {i} worker pool and drain its queue", tags=["ops"])
    return m, sid, bid


def _inside(m, query, target):
    return target in [h["id"] for h in m.recall(query, k=10, influence_only=True, reinforce=False)]


def _earn(m, target, query, n=5, sleep=0.0):
    """Legitimate successful use: the memory is recalled and the work succeeds."""
    import time as _t
    got = 0
    for _ in range(n):
        ids = [h["id"] for h in m.recall(query, k=3)]
        if target in ids:
            m.credit(ids, True)
            got += 1
        if sleep:
            _t.sleep(sleep)
    return got


def _suppress(m, sid, cap, sleep=0.0):
    """Adversary episodes until the safety memory stops being served under the influence gate."""
    import time as _t
    for ep in range(1, cap + 1):
        ids = [h["id"] for h in m.recall(ATTACK_Q, k=3)]
        m.credit(ids, False)
        if sleep:
            _t.sleep(sleep)
        if not _inside(m, HONEST_Q, sid):
            return ep
    return None


def test_t1_default_off_is_byte_identical(tmp_path):
    """OFF must add no keys, change no counts, and keep credit()'s return shape."""
    m, sid, _ = _store(None, str(tmp_path))
    assert _earn(m, sid, HONEST_Q, 5) == 5
    ret = m.credit([sid], False)
    rec = {r["id"]: r for r in m.items}[sid]
    assert float(rec["good"]) == 5.0
    assert float(rec["bad"]) == 1.0
    assert "credit_seen" not in rec, "OFF must not write the burst-tracking key onto records"
    assert set(ret) == {"updated", "outcome", "weight"}, "OFF must not change the return shape"


def test_t2_suppression_costs_more_with_the_window_on(tmp_path):
    """The whole point: a burst inside one window must count once."""
    a, sid_a, _ = _store(None, str(tmp_path / "off"))
    _earn(a, sid_a, HONEST_Q, 5)
    assert _inside(a, HONEST_Q, sid_a), "control: must start inside the gate, else eviction is vacuous"
    off = _suppress(a, sid_a, cap=200)
    assert off is not None, "control: the attack must land with the window OFF"

    b, sid_b, _ = _store(3600, str(tmp_path / "on"))
    _earn(b, sid_b, HONEST_Q, 5)
    assert _inside(b, HONEST_Q, sid_b)
    on = _suppress(b, sid_b, cap=200)

    assert on is None or on > off, f"window on must cost more than off (off={off}, on={on})"


def test_t2b_the_attack_is_targeted_not_a_gate_collapse(tmp_path):
    """An equally-credited memory the adversary does not target must be untouched."""
    m, sid, bid = _store(None, str(tmp_path))
    _earn(m, sid, HONEST_Q, 5)
    _earn(m, bid, BYSTANDER_Q, 5)
    assert _inside(m, BYSTANDER_Q, bid), "control: bystander must start inside, else it has nothing to lose"
    assert _suppress(m, sid, cap=200) is not None
    assert _inside(m, BYSTANDER_Q, bid), "suppression must not evict an untargeted memory"


def test_t3_separate_occasions_still_accumulate(tmp_path):
    """If the window blocked legitimate learning, it would buy resistance by breaking the product."""
    m, sid, _ = _store(0.05, str(tmp_path))
    got = _earn(m, sid, HONEST_Q, 5, sleep=0.06)     # each credit lands outside the previous window
    rec = {r["id"]: r for r in m.items}[sid]
    assert got == 5
    assert float(rec["good"]) == 5.0, "credit on separate occasions must accumulate normally"


def test_t4_it_is_cost_not_prevention(tmp_path):
    """MUST still land. If this ever passes by NOT landing, the flag has silently disabled demotion."""
    m, sid, _ = _store(0.05, str(tmp_path))
    _earn(m, sid, HONEST_Q, 5, sleep=0.06)
    assert _inside(m, HONEST_Q, sid)
    landed = _suppress(m, sid, cap=80, sleep=0.06)
    assert landed is not None, ("the attack must remain possible across separate occasions — this is a "
                               "cost increase, not immunity; a None here means demotion was disabled")


def test_collapsed_ids_are_reported_to_the_caller(tmp_path):
    """A silently dropped credit is indistinguishable from an applied one, so credit() must say so."""
    m, sid, _ = _store(3600, str(tmp_path))
    first = m.credit([sid], False)
    second = m.credit([sid], False)
    rec = {r["id"]: r for r in m.items}[sid]
    assert first["updated"] == [sid] and "collapsed" not in first
    assert second["updated"] == [] and second["collapsed"] == [sid]
    assert float(rec["bad"]) == 1.0, "the second credit inside the window must not have counted"


def test_opposite_polarity_is_not_collapsed(tmp_path):
    """The window is per-polarity: a good must not be swallowed by a recent bad."""
    m, sid, _ = _store(3600, str(tmp_path))
    m.credit([sid], False)
    m.credit([sid], True)
    rec = {r["id"]: r for r in m.items}[sid]
    assert float(rec["bad"]) == 1.0 and float(rec["good"]) == 1.0


def test_window_survives_a_reload(tmp_path):
    """credit_seen rides on the record, so a restart must not reset an attacker's window."""
    path = os.path.join(str(tmp_path), "s.json")
    m = Inspeximus(path)
    m.credit_burst_window = 3600
    sid = m.remember(SAFETY, tags=["safety"])
    sid = sid["id"] if isinstance(sid, dict) else sid
    m.credit([sid], False)
    m.flush()

    m2 = Inspeximus(path)
    m2.credit_burst_window = 3600
    ret = m2.credit([sid], False)
    rec = {r["id"]: r for r in m2.items}[sid]
    assert ret["updated"] == [] and ret.get("collapsed") == [sid], "the window must survive a reload"
    assert float(rec["bad"]) == 1.0
