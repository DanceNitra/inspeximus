"""The runner-up mutation survivors: fork proof, bitemporal replay, the two gates, and a compliance count.

Same discipline as test_mutation_gaps.py -- each test is written from the CONTRACT, read first, then from
the behaviour a specific mutant breaks. The previous round cost five rewrites because I asserted before
reading; this one reads `both_cosigned`, `believed_at`, `_gated_links`, `_grants_full` and
`controls_with_evidence` first.

Each test states the mutation it must fail against so a later reader can re-verify the teeth.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus

_HAVE_ED = getattr(core, "_HAVE_ED", False)


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _m(**kw):
    return Inspeximus(path=_path(), **kw)


# ── detect_split_view: both_cosigned needs BOTH sides ───────────────────────────────────────────────
@pytest.mark.skipif(not _HAVE_ED, reason="ed25519 backend not available")
def test_both_cosigned_is_false_when_only_one_head_is_cosigned():
    """MUTANT: `bool(va["ok"] and vb["ok"])` -> `or`, which reports both heads independently witnessed when
    only one is. An auditor reads `both_cosigned` as "two witnessed views exist to compare"; getting it
    from one signature turns a single signed head into apparent evidence of a fork."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sk = Ed25519PrivateKey.generate()
    sk_hex = sk.private_bytes_raw().hex()
    pub = sk.public_key().public_bytes_raw().hex()

    m = _m(receipts=True)
    m.remember("first fact")
    a = m.anchor()
    m.remember("second fact")
    b = m.anchor()

    # cosignatures are (pubkey_hex, sig_hex) PAIRS -- a bare signature string is silently skipped as
    # malformed (the verifier never crashes), so my first version got both_cosigned False on BOTH calls
    # and would have "passed" the mutant it exists to kill.
    cosig_a = (pub, core.witness_cosign(sk_hex, a))
    res = Inspeximus.detect_split_view(a, [cosig_a], b, [], witnesses=[pub])
    assert res["both_cosigned"] is False, res

    cosig_b = (pub, core.witness_cosign(sk_hex, b))
    res2 = Inspeximus.detect_split_view(a, [cosig_a], b, [cosig_b], witnesses=[pub])
    assert res2["both_cosigned"] is True, res2


@pytest.mark.skipif(not _HAVE_ED, reason="ed25519 backend not available")
def test_two_consistent_heads_are_not_a_fork():
    """The proof must not fire on an honest append-only pair, or it is an alarm, not evidence."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sk = Ed25519PrivateKey.generate()
    sk_hex, pub = sk.private_bytes_raw().hex(), sk.public_key().public_bytes_raw().hex()
    m = _m(receipts=True)
    m.remember("first fact")
    a = m.anchor()
    m.remember("second fact")
    b = m.anchor()
    res = Inspeximus.detect_split_view(a, [(pub, core.witness_cosign(sk_hex, a))], b,
                                       [(pub, core.witness_cosign(sk_hex, b))], witnesses=[pub])
    assert res["fork"] is False, res
    assert res["both_cosigned"] is True, "the honest pair must still be recognised as two witnessed views"


# ── believed_at: the right key, and only what was recorded by then ──────────────────────────────────
def test_believed_at_answers_for_the_requested_key_only():
    """MUTANT: the `r.get("key") == key` filter matches the wrong key, so a replay of what the agent
    believed returns another key's value entirely."""
    m = _m()
    m.remember("the retry limit is 3", key="policy::retries", object="3")
    m.remember("the timeout is 30s", key="policy::timeout", object="30s")

    res = m.believed_at("policy::retries", as_recorded=core.time.time() + 1)
    assert res is not None and res["object"] == "3", res


def test_believed_at_ignores_a_correction_recorded_later():
    """The whole point: transaction-time replay. A correction written AFTER the moment must be invisible."""
    m = _m()
    m.remember("the retry limit is 3", key="policy::retries", object="3")
    cut = core.time.time()
    core.time.sleep(0.01)
    m.remember("the retry limit is 9", key="policy::retries", object="9")

    assert m.believed_at("policy::retries", as_recorded=cut)["object"] == "3"
    assert m.believed_at("policy::retries", as_recorded=core.time.time() + 1)["object"] == "9"


def test_believed_at_returns_none_before_the_key_existed():
    m = _m()
    before = core.time.time()
    core.time.sleep(0.01)
    m.remember("the retry limit is 3", key="policy::retries", object="3")
    assert m.believed_at("policy::retries", as_recorded=before) is None


# ── the coherence gate must actually drop off-topic witnesses ───────────────────────────────────────
def test_the_coherence_gate_drops_an_off_topic_witness():
    """MUTANT: the `>= self.coherence_gate` comparison admits every link, so an off-topic record
    corroborates and the >=2-distinct-source path is reopened to anything.

    Assert BOTH halves: the on-topic witness survives the gate and the off-topic one does not. Asserting
    only the drop would also pass against a gate that drops everything."""
    m = _m()
    subject = m.remember("the deploy target is prod", source={"doc": "a.example"})
    on_topic = m.remember("deploy target confirmed as prod", source={"doc": "b.example"})
    off_topic = m.remember("the cafeteria serves soup on tuesdays", source={"doc": "c.example"})

    rec = next(r for r in m._items if r["id"] == subject)
    rec["links"] = [on_topic, off_topic]
    by_id = {r["id"]: r for r in m.items}

    assert set(m._gated_links(rec, by_id)) == {on_topic, off_topic}, "gate off -> links unchanged"

    m.coherence_gate = 0.2
    gated = set(m._gated_links(rec, by_id))
    assert on_topic in gated, "an on-topic witness must survive the gate"
    assert off_topic not in gated, "an off-topic witness must be dropped"


# ── require_earned: an unearned source does not get the full cap ────────────────────────────────────
def test_require_earned_does_not_grant_the_full_cap_when_bad_exceeds_good():
    """MUTANT: `good > 0 and good >= bad` -> `or`, which hands the full lifetime budget to a source whose
    outcomes are net-negative -- exactly the sleeper this cap exists to bound.

    `require_earned` only bites when `provenance_lo` is set (otherwise every source gets the full budget by
    construction), so the fixture must set it or the test proves nothing."""
    m = _m()
    rid = m.remember("a claim from a net-negative source", source={"doc": "sleeper.example"})
    for _ in range(5):
        m.credit([rid], outcome=False)
    m.credit([rid], outcome=True)
    rec = next(r for r in m.items if r["id"] == rid)
    assert rec.get("bad", 0) > rec.get("good", 0), "fixture must be net-negative, or nothing is tested"

    res = m.spend_irreversible([rid], amount=0.5, budget=1.0,
                               provenance_lo=0.1, require_earned=True)
    assert res["allowed"] is False, \
        f"a net-negative source must be capped at provenance_lo (0.1), not granted the 1.0 budget: {res}"


def test_require_earned_still_grants_the_full_cap_to_an_earned_source():
    """The gate must not become 'deny everything' -- that passes the test above for the wrong reason."""
    m = _m()
    rid = m.remember("a claim from an earning source", source={"doc": "honest.example"})
    for _ in range(5):
        m.credit([rid], outcome=True)
    res = m.spend_irreversible([rid], amount=0.5, budget=1.0,
                               provenance_lo=0.1, require_earned=True)
    assert res["allowed"] is True, res


# ── compliance: the evidence count must count evidence ──────────────────────────────────────────────
def test_controls_with_evidence_counts_only_controls_with_evidence():
    """MUTANT: `c["status"] == "evidence"` -> `!=`, which reports the controls that have NO evidence as
    the ones that do -- an auditor-facing number that inverts its own meaning. Derive the expected count
    from the controls list itself rather than hard-coding it, so it survives new controls being added."""
    m = _m(receipts=True)
    m.remember("a fact", source={"doc": "a.example"})
    m.forget_subject("a.example", request_id="R1", basis="gdpr-art17", allow_ambiguous=True)

    from inspeximus.compliance import compliance_report
    rep = compliance_report(m)
    controls = rep["controls"]
    expected = sum(1 for c in controls if c["status"] == "evidence")
    assert rep["summary"]["controls_with_evidence"] == expected

    assert 0 < expected < len(controls), \
        (f"fixture must produce a MIX ({expected}/{len(controls)}), or == and != are indistinguishable "
         "and the mutant survives")


def test_require_earned_denies_a_two_witness_sybil_that_corroboration_accepts():
    """MUTANT: `if require_earned:` removed, so the grant falls through to `_corroborated`.

    The previous test cannot kill that one: its record is net-negative, so BOTH paths deny and the two
    behaviours are indistinguishable. This is the case the flag was written for, in its own words -- "a
    forged/attested >=2-witness sybil clears _corroborated but not this": corroborated through the
    >=2-distinct-source path, with zero earned outcome.
    """
    m = _m()
    rid = m.remember("the payout wallet is 0xEVIL", source={"doc": "sybil-a.example"})
    w1 = m.remember("confirming the payout wallet", source={"doc": "sybil-b.example"})
    w2 = m.remember("also confirming the payout wallet", source={"doc": "sybil-c.example"})
    rec = next(r for r in m._items if r["id"] == rid)
    rec["links"] = [w1, w2]                       # two DISTINCT sources, no earned outcome at all

    by_id = {r["id"]: r for r in m.items}
    assert m._corroborated(rec, by_id) is True, "fixture must clear the forgeable path, or nothing is tested"
    assert float(rec.get("good", 0) or 0) == 0.0, "and must have earned nothing"

    lenient = m.spend_irreversible([rid], amount=0.5, budget=1.0,
                                   provenance_lo=0.1, require_earned=False)
    assert lenient["allowed"] is True, f"corroboration alone grants the full budget: {lenient}"

    strict = _m()
    rid2 = strict.remember("the payout wallet is 0xEVIL", source={"doc": "sybil-a.example"})
    x1 = strict.remember("confirming the payout wallet", source={"doc": "sybil-b.example"})
    x2 = strict.remember("also confirming the payout wallet", source={"doc": "sybil-c.example"})
    next(r for r in strict._items if r["id"] == rid2)["links"] = [x1, x2]
    res = strict.spend_irreversible([rid2], amount=0.5, budget=1.0,
                                    provenance_lo=0.1, require_earned=True)
    assert res["allowed"] is False, \
        f"require_earned must NOT accept a two-witness sybil with no earned outcome: {res}"
