"""Support-keyed read-path reopen (marintkael's fix, r/RAG 2026-07-16): key reopen on NOVELTY-OF-SUPPORT, not on
value. A restatement whose grounds the ledger has already seen (or that carries no support) is an ECHO and is
silenced even though it disagrees on value; only a contradiction resting on grounds NOT in the justification set
reopens, and corroboration counts DISTINCT novel supports. So replay collapses into echo by construction and the
value-disagreement DoS lever falls off, while an honest late correction bringing new ground still gets through."""
from mnemo import Mnemo


def _store(tmp_path):
    return Mnemo(str(tmp_path / "m.json"))


def test_two_distinct_novel_grounds_reopen(tmp_path):
    m = _store(tmp_path)
    m.remember("the region is Frankfurt", key="a/region", object="Frankfurt")
    r1 = m.observe("actually Ohio", key="a/region", object="Ohio", support="shipping-manifest-42")
    assert r1["reopened"] is False and r1["pending"] == 1           # one distinct ground so far
    r2 = m.observe("it's Ohio", key="a/region", object="Ohio", support="tax-filing-2026")
    assert r2["reopened"] is True                                   # a second DISTINCT ground -> reopen
    assert m.reopened()[0]["reason"] == "novel_support_contradiction"


def test_replay_same_ground_collapses_to_echo(tmp_path):
    """The value-disagreement DoS lever: replaying the old value never reopens, because the SAME ground is one
    distinct signature no matter how many times it is emitted."""
    m = _store(tmp_path)
    m.remember("color is green", key="u/color", object="green")
    first = m.observe("no it's blue", key="u/color", object="blue", support="ticket-9")
    assert first["reopened"] is False and first["pending"] == 1
    for _ in range(6):                                             # replay the SAME contradiction+ground
        r = m.observe("no it's blue", key="u/color", object="blue", support="ticket-9")
        assert r["reopened"] is False and r.get("echo") is True    # already-seen ground -> echo
    assert m.reopened() == []                                      # DoS lever falls off: never reopens


def test_restatement_without_support_is_echo(tmp_path):
    m = _store(tmp_path)
    m.remember("x is 1", key="k/x", object="1")
    r = m.observe("2", key="k/x", object="2", support=[])          # contradicts on value, brings no ground
    assert r["reopened"] is False and r.get("echo") is True


def test_honest_late_correction_with_new_ground_gets_through(tmp_path):
    m = _store(tmp_path)
    m.remember("status is open", key="t/status", object="open")
    m.observe("it's closed", key="t/status", object="closed", support="audit-log")
    r = m.observe("closed per the incident report", key="t/status", object="closed", support="incident-3391")
    assert r["reopened"] is True                                   # two independent grounds -> real contradiction


def test_agreeing_observation_marks_ground_seen(tmp_path):
    """A ground cited in agreement is now 'seen and discounted', so a later contradiction citing the SAME ground
    is an echo, not novel."""
    m = _store(tmp_path)
    m.remember("owner is Alice", key="o/owner", object="Alice")
    m.observe("still Alice", key="o/owner", object="Alice", support="handover-doc")   # agrees, ground seen
    r = m.observe("no, Bob", key="o/owner", object="Bob", support="handover-doc")     # same ground, now contradicts
    assert r["reopened"] is False and r.get("echo") is True


def test_value_obscuring_revert_needs_new_ground(tmp_path):
    """Under support-keying a bare 'go back' (no novel ground) is an echo, not a free reopen; a revert that
    brings new ground reopens and surfaces the prior value."""
    m = _store(tmp_path)
    m.remember("plan is A", key="p/plan", object="A")
    m.remember("plan is B", key="p/plan", object="B")             # B superseded A
    bare = m.observe("go back to the old plan", key="p/plan", object=None, support=[])
    assert bare["reopened"] is False and bare.get("echo") is True  # no ground -> not a free reopen
    g1 = m.observe("revert, see rollback ticket", key="p/plan", object=None, support="rollback-1")
    g2 = m.observe("revert, see postmortem", key="p/plan", object=None, support="postmortem-7")
    assert g2["reopened"] is True and g2["surfaced_prior"] == "A"


def test_support_mode_does_not_affect_value_mode_legacy(tmp_path):
    """Omitting support keeps the byte-identical 1.9.2 value-keyed behavior."""
    m = _store(tmp_path)
    m.remember("v is 1", key="k/v", object="1")
    m.observe("2", key="k/v", object="2"); r = m.observe("2", key="k/v", object="2")   # no support -> legacy
    assert r["reopened"] is True and r["reason"] if False else m.reopened()[0]["reason"] == "corroborated_contradiction"
