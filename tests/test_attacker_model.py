"""The four attacker findings, each reproduced before being acted on.

They were reported by an adversarial review and carried in the handoff as UNVERIFIED. All four hold. Three are
inherent to a store with no writer identity, so the fix is an accurate disclosure plus a test that the
disclosure stays true; the fourth turned a hostile write into a permanent block on a legal obligation, and
that one got code.

The attacker model throughout: someone who can call `remember()` / `credit()` through the normal API — a
compromised agent, a hostile document in a RAG corpus — who does NOT hold `receipt_key`.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import AmbiguousSubject, Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── 1. a standing promotion leaves no evidence ──────────────────────────────────────────────────────
def test_credit_changes_standing_but_leaves_no_audit_trail():
    """`credit()` sets `good`, which the influence gate and corroboration check read — so it decides whether
    a record can be served under `recall(influence_only=True)`. It emits no receipt and `state_digest` does
    not cover standing, so the promotion is invisible to every integrity surface this library sells.

    The self-grading risk was already disclaimed (`credit_requires_warrant`); the absence of any
    after-the-fact trace was not."""
    m = Inspeximus(path=_path(), receipts=True)
    evil = m.remember("poisoned fact", source={"doc": "evil.example"})
    receipts_before, digest_before = len(m._receipts), m.state_digest()

    m.credit([evil], outcome=True, weight=1e6)

    rec = next(r for r in m.items if r["id"] == evil)
    assert rec.get("good") == 1e6, "the promotion must actually happen, or this test proves nothing"
    assert len(m._receipts) == receipts_before, "no receipt — the documented limit"
    assert m.state_digest() == digest_before, "no digest change — the documented limit"
    assert m.verify_writes()[0] is True, "and integrity still reports clean"

    assert "NO EVIDENCE TRAIL" in (Inspeximus.credit.__doc__ or "")


# ── 2. derived_from inherits a source you do not own ────────────────────────────────────────────────
def test_derived_from_lets_an_attacker_spend_another_sources_budget():
    """Taint inheritance is deliberate — a summary must charge its origins — but nothing checks that the
    writer was entitled to derive from that parent. So naming a trusted record as a parent makes the
    attacker's record attributable to it, and the irreversible budget is keyed on exactly that."""
    m = Inspeximus(path=_path(), receipts=True)
    audited = m.remember("Audited figure: revenue 10M", source={"doc": "bigfour-auditor.com"})
    evil = m.remember("Revenue is 900M", source={"doc": "evil.example"}, derived_from=[audited])

    sources = Inspeximus._rec_sources(next(r for r in m.items if r["id"] == evil))
    assert "bigfourauditor" in sources, sources

    assert m.spend_irreversible([evil], amount=1.0, budget=1.0)["allowed"] is True
    assert m.spend_irreversible([audited], amount=1.0, budget=1.0)["allowed"] is False, \
        "the real owner is denied by the attacker's spend"

    assert "SPENDS THAT" in (Inspeximus._supersede_by_key.__doc__ or ""), \
        "the limit must be stated where the unauthenticated-write limit is"


# ── 3. one hostile write blocked every later DSAR — this one got code ───────────────────────────────
def _victim_and_attacker():
    m = Inspeximus(path=_path(), receipts=True)
    victim = m.remember("Alice lives at 12 Oak St", source={"doc": "user-42"})
    m.remember("Alice summary", derived=True, derived_from=[victim], source={"doc": "summary-svc"})
    m.remember("attacker junk", source={"doc": "User_42"})       # canonicalises onto the victim
    return m


def test_a_hostile_write_still_makes_the_default_erasure_refuse():
    """The guard is right by default — erasing both subjects on one DSAR is the worse outcome."""
    m = _victim_and_attacker()
    with pytest.raises(AmbiguousSubject):
        m.forget_subject("user-42", request_id="DSAR-1", basis="gdpr-art17")
    assert any("Oak St" in r["text"] for r in m.items)


def test_exact_mode_lets_the_dsar_complete_without_collateral():
    """A guard that cannot be satisfied turns one hostile write into a permanent block on a legal
    obligation. `exact=True` proceeds on the collision-safe subset the resolver had already computed:
    the victim's records and what inherited from them, leaving the colliding source alone."""
    m = _victim_and_attacker()
    res = m.forget_subject("user-42", request_id="DSAR-1", basis="gdpr-art17", exact=True)

    assert res["erased"] == 2, "the victim's record AND its derived summary"
    assert not any("Oak St" in r["text"] for r in m.items)
    assert any("attacker junk" in r["text"] for r in m.items), "the other subject must survive"


def test_allow_ambiguous_still_erases_everything_deliberately():
    """The blunt escape stays available and stays blunt — the two modes must remain distinguishable."""
    m = _victim_and_attacker()
    assert m.forget_subject("user-42", request_id="DSAR-1", basis="gdpr-art17",
                            allow_ambiguous=True)["erased"] == 3


# ── 4. capacity turns a growth attack into a targeted delete ────────────────────────────────────────
def test_capacity_lets_an_attacker_evict_specific_records():
    """SECURITY.md told you to set `capacity=` against exhaustion. Eviction ranks by `value`, which is
    caller-supplied and unbounded, and the two-tier policy protects the top slice BY RAW VALUE — exactly
    what the attacker buys. The mitigation is the weapon."""
    m = Inspeximus(path=_path(), capacity=10)
    victims = [m.remember(f"victim record {i}", value=1.0) for i in range(5)]
    for i in range(50):
        m.remember(f"attacker flood {i}", value=1000.0)

    survived = [v for v in victims if any(r["id"] == v for r in m.items)]
    assert survived == [], f"{len(survived)} of 5 survived — if this changes, update SECURITY.md"
    assert len([r for r in m.items if r["status"] == "active"]) == 10


def test_security_md_states_the_capacity_tradeoff():
    """A recommendation that creates a worse problem than it solves must say so where it is made."""
    doc = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "SECURITY.md"), encoding="utf-8").read()
    assert "TARGETED DELETE" in doc
    assert "5 of 5" in doc, "the measured result belongs next to the claim"
