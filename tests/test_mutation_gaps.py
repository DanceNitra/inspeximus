"""Tests written FROM surviving mutants, not from the code.

A mutation pass scored 36.4% (51/140) and left 89 survivors, of which 47 were triaged as real gaps. Each
test below is the one named in the triage as the test that would kill a specific survivor — written from
the BEHAVIOUR the mutant breaks, never from the spelling of the line, so it stays valid when the line
moves. (The line numbers recorded with the survivors are already stale: the file shifted in 1.68.0, and
what was core.py:3662 is now a comment. Anchoring a test to a line number is how a suite quietly stops
testing what it claims.)

Each test names the mutation it must fail against, so a later reader can re-verify the teeth.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _m(**kw):
    return Inspeximus(path=_path(), **kw)


# ── route() has no delete intent, and that is worth pinning down ────────────────────────────────────
def test_route_classifies_a_delete_utterance_as_an_assertion():
    """The triage said `route("delete X")` should forget that key's active rows. It does not: route's
    documented intent set is assert / correct / revert / echo, with no delete, so "delete api::key" is
    classified `assert` and STORED as a memory. Measured, not assumed.

    Kept as a characterisation test rather than deleted, because the behaviour is surprising for a product
    that sells erasure: a user saying "delete my email address" gets it remembered. If a delete intent is
    ever added, this test should fail and be rewritten -- that is the point of pinning it."""
    m = _m()
    m.remember("the api key is abc123", key="api::key", object="abc123")
    res = m.route("delete api::key")
    assert res.get("intent") == "assert" and res.get("action") == "remembered", res
    assert any(r["id"] == res["id"] for r in m.items), "the utterance itself is stored"


# ── the certificate's absence proof on a PLAINTEXT store ────────────────────────────────────────────
def test_certificate_fails_when_an_allegedly_erased_id_is_still_in_a_plaintext_store():
    """MUTANT: `==` -> `!=` on the encryption-magic check, which flips which branch runs. Today the
    encrypted branch SKIPS the absence proof, so inverting it hides the check entirely and the suite
    stays green. The kill is a plaintext store that still holds an id the certificate says was erased."""
    m = _m(receipts=True)
    victim = m.remember("alice lives at 12 Oak St", source={"doc": "user-42"})
    m.remember("something else")
    m.forget_subject("user-42", request_id="R1", basis="gdpr-art17")
    cert = m.erasure_certificate()
    assert core.verify_erasure_certificate(cert, store_items=m.items)["valid"] is True

    resurrected = list(m.items) + [{"id": victim, "text": "alice lives at 12 Oak St",
                                    "status": "active", "ts": 0.0}]
    res = core.verify_erasure_certificate(cert, store_items=resurrected)
    assert res["valid"] is False, "an id the certificate says is gone must not be present in the store"
    assert any("present" in p.lower() or "absent" in p.lower() for p in res["problems"]), res["problems"]


# ── revert() picks the record THIS one superseded ───────────────────────────────────────────────────
def test_revert_restores_the_record_the_current_value_superseded():
    """MUTANT: `and` -> `or` in the predecessor search, which lets it match any earlier row on the key
    instead of the one the current record actually retired. The kill needs a chain of THREE."""
    m = _m()
    m.remember("wallet is 0xAAA", key="payout::wallet", object="0xAAA")
    second = m.remember("wallet is 0xBBB", key="payout::wallet", object="0xBBB")
    m.remember("wallet is 0xCCC", key="payout::wallet", object="0xCCC")

    res = m.revert(key="payout::wallet")

    # revert restores through the sanctioned reaffirm channel, so it writes a NEW record carrying the old
    # value rather than reactivating the old row -- the flip stays in the ledger. Assert the VALUE, not the
    # id; my first version asserted `active[0]["id"] == second` and failed against correct behaviour.
    assert res["reverted_to_object"] == "0xBBB", res
    active = [r for r in m.items if r.get("key") == "payout::wallet" and r["status"] == "active"]
    assert len(active) == 1 and active[0].get("object") == "0xBBB"
    assert active[0]["id"] != second, "the original row stays superseded; the restore is a new ledger entry"
    _ = second


# ── recall filters ──────────────────────────────────────────────────────────────────────────────────
def test_where_in_filter_excludes_the_complement():
    """MUTANT: `not in` -> `in`, which inverts the filter. A test that only asserts the wanted rows are
    present passes under the inversion; the kill asserts the complement is ABSENT."""
    m = _m()
    m.remember("alpha fact", meta={"topic": "alpha"})
    m.remember("beta fact", meta={"topic": "beta"})
    hits = m.recall("fact", k=10, where={"topic": {"$in": ["alpha"]}}) or []
    texts = [h.get("text") for h in hits]
    assert any("alpha" in t for t in texts)
    assert not any("beta" in t for t in texts), "the complement must be excluded, not merely out-ranked"


def test_a_slashed_record_is_dropped_from_the_influence_path():
    """MUTANT: `slashed or orphan` -> `slashed and orphan` in the influence gate, so a slashed record that
    still has lineage is treated as warranted.

    The record must be CORROBORATED first, or the test passes for the wrong reason: my first version
    slashed a bare record, which influence_only drops for having no corroboration at all, so the slash
    condition was never exercised and the mutant survived. Establish that it IS served, then slash.
    """
    m = _m()
    rid = m.remember("revoked claim about widgets", source={"doc": "evil.example"})
    m.credit([rid], outcome=True)                    # -> corroborated, so it reaches the influence path
    assert any(h["id"] == rid for h in (m.recall("widgets", k=10, influence_only=True) or [])),         "fixture must be served on the influence path BEFORE the slash, or the slash proves nothing"

    m.slash([rid], scope="memory")

    # slash() deliberately KEEPS the record readable -- its own docstring: "WHY not forget(): forget()
    # deletes; slash() KEEPS the records for audit". What it must fail is the INFLUENCE gate. The triage
    # line said plain recall(), which overstated the contract.
    assert any(h["id"] == rid for h in (m.recall("widgets", k=10) or [])),         "slash must not delete -- keeping the record for audit is the point"
    assert not any(h["id"] == rid for h in (m.recall("widgets", k=10, influence_only=True) or [])),         "a slashed record must not be served on the influence path"

    # THE discriminating half. slash() also zeroes `good` and books a dominating `bad`, so the assertion
    # above is satisfied by the CORROBORATION term alone -- the `slashed` term is never exercised and the
    # mutant `slashed or orphan` -> `slashed and orphan` survives it. Credit the standing back up until
    # corroboration passes again: now only the landed retraction can keep it off the influence path.
    rec = next(r for r in m.items if r["id"] == rid)
    for _ in range(int(rec.get("bad", 0)) + 5):
        m.credit([rid], outcome=True)
    rec = next(r for r in m.items if r["id"] == rid)
    assert rec.get("good", 0) >= rec.get("bad", 0), "fixture must restore corroboration, or this proves nothing"
    assert (rec.get("meta") or {}).get("slashed") is True, "and the retraction must still be on record"
    assert not any(h["id"] == rid for h in (m.recall("widgets", k=10, influence_only=True) or [])),         "a LANDED RETRACTION must keep a record off the influence path even once standing is re-earned"


# ── credit accumulates ──────────────────────────────────────────────────────────────────────────────
def test_credit_accumulates_rather_than_resetting():
    """MUTANT: `or` -> `and` in the accumulation, turning `good = good + 1` into an assignment. Two
    credits must reach 2.0, not 1.0."""
    m = _m()
    rid = m.remember("a fact worth crediting")
    m.credit([rid], outcome=True)
    m.credit([rid], outcome=True)
    rec = next(r for r in m.items if r["id"] == rid)
    assert rec.get("good") == pytest.approx(2.0), rec.get("good")


# ── graduation is gated on evidence, not just value ─────────────────────────────────────────────────
def test_a_contradicted_single_source_record_does_not_graduate():
    """MUTANT: `>=` -> `<` on the graduation guard. A record at graduate-level value but with bad > good
    and fewer than two distinct sources must stay episodic."""
    m = _m()
    rid = m.remember("a shaky claim", mtype="episodic", source={"doc": "only.example"})
    for _ in range(30):
        m.credit([rid], outcome=False)
    for _ in range(3):
        m.credit([rid], outcome=True)
    rec = next(r for r in m.items if r["id"] == rid)
    assert rec.get("bad", 0) > rec.get("good", 0), "fixture must actually be contradicted"
    assert rec["mtype"] == "episodic", "a contradicted single-source record must not graduate"


# ── retention drops only what it says it drops ──────────────────────────────────────────────────────
def test_retention_drops_only_stale_episodic():
    """MUTANT: `and` -> `or` in the retention predicate, which widens the sweep to semantic/procedural."""
    m = _m()
    ep = m.remember("an episodic detail", mtype="episodic")
    sem = m.remember("a semantic fact", mtype="semantic")
    proc = m.remember("a procedural step", mtype="procedural")
    for r in m._items:
        r["ts"] = r["last_access"] = 0.0                    # ancient

    m.apply_retention(max_age_days=1, drop_stale_episodic=True)

    alive = {r["id"] for r in m.items}
    assert sem in alive and proc in alive, "only episodic may be dropped"
    assert ep not in alive


# ── verify_claim: tenant scope and the unsupported/contradicted boundary ────────────────────────────
def test_verify_claim_is_tenant_scoped_on_the_keyless_path():
    """MUTANT: `or` -> `and` in the tenant filter, which lets one tenant's claim be judged against
    another's records. Both halves matter: it must not see tenant b, and must still see its own."""
    m = _m()
    a, b = m.for_tenant("acme"), m.for_tenant("globex")
    b.remember("the maximum retry count is 9")
    a.remember("the maximum retry count is 3")

    own = a.verify_claim("the maximum retry count is 3")
    assert own.get("verdict") in ("supported", "corroborated"), own

    cross = a.verify_claim("the maximum retry count is 9")
    assert cross.get("verdict") != "supported", "tenant a must not be supported by tenant b's record"


def test_verify_claim_returns_unsupported_not_contradicted_for_a_merely_similar_record():
    """MUTANT: `and` -> `or` in the contradiction test, which reports any lexically similar record as a
    contradiction. A record about a different subject must be `unsupported`."""
    m = _m()
    m.remember("the staging database runs postgres 14")
    res = m.verify_claim("the analytics warehouse runs postgres 16")
    assert res.get("verdict") != "contradicted", res


# ── index coherence must not pass on missing vectors ────────────────────────────────────────────────
def test_index_coherence_reports_incoherent_when_vectors_are_missing():
    """MUTANT: `and` -> `or`, which lets a matching recipe alone declare the index coherent even when the
    vectors it describes are absent."""
    m = _m()
    for i in range(5):
        m.remember(f"fact number {i}")

    # With NO embedder configured there is nothing to be incoherent about, and it correctly reports
    # coherent. My first version asserted False here and was simply wrong about the contract -- the
    # docstring says it reports records "missing a vector WHILE AN EMBEDDER" is configured.
    assert m.index_coherence()["coherent"] is True, "no embedder -> nothing to be incoherent about"

    # The hook is `embed`, not `embedder` -- my first attempt set the wrong attribute, the report still
    # said embedder_configured=False, and the assertion failed against correct behaviour. Assert the
    # PRECONDITION landed before asserting the outcome.
    m.embed = lambda t: [0.1, 0.2, 0.3]
    m._persist_vectors = True
    res = m.index_coherence()
    assert res["embedder_configured"] is True, res
    assert res["missing_vecs"] == 5, res
    assert res["coherent"] is False, res


# ── distill_and_remember rejects a too-short support quote ──────────────────────────────────────────
def test_distill_rejects_a_support_quote_that_is_too_short():
    """MUTANT: the 12-character minimum in `_support_ok` -> 0, which admits a token as 'support'. The
    floor exists so a quote has to be a quote.

    `distiller` is called as `distiller(prompt, text)` -- TWO arguments. A one-argument stub raises, and
    the call is deliberately FAIL-OPEN, so it returns `error: distiller_failed` with everything at zero:
    a dropped-count assertion would have "passed" against a stub that never ran. That is the shape this
    whole file exists to avoid, so the happy path is asserted first."""
    m = _m()
    passage = "The retention window is 30 days, per the 2026 policy revision."

    ok = m.distill_and_remember(
        passage,
        lambda _prompt, _text: [{"text": "the retention window is 30 days",
                                 "support": "The retention window is 30 days, per the 2026 policy"}],
        source={"doc": "policy"})
    assert not ok.get("error"), ok
    assert ok["captured"] >= 1, ok

    tiny = m.distill_and_remember(
        passage,
        lambda _prompt, _text: [{"text": "the retention window is 30 days", "support": "30 d"}],
        source={"doc": "policy"})
    assert not tiny.get("error"), tiny
    assert tiny["captured"] == 0 and tiny.get("dropped", 0) >= 1,         f"a 4-character support quote must not be accepted as evidence: {tiny}"
