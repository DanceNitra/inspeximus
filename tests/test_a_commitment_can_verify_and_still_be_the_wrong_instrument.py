"""A commitment can verify perfectly and be evidentially insufficient for the question asked of it.

@safal207's compact form, on anthropics/claude-code#34556: **cryptographic validity is not
evidentiary sufficiency. A commitment must cover exactly the fields the claimed predicate depends
on.** @Stratogain arrived at the same place from his own store and named the failure precisely: a
commitment over the KEY SET says valid for every one of the 226 of 634 paths whose CONTENT changed.
For headroom that is correct -- headroom is a property of the keys and content cannot touch it. For
"is this observation still good" it is exactly wrong. Same hash, same store, opposite verdicts.

It is ours too. `identifier_contract()` has returned `population_commitment` since 2.18.0, and its
own `limits` prose already said a caller keying on something else "would hold a commitment over the
wrong set, and it would verify clean every time" -- which is the finding, sitting in a paragraph no
verifier can query.

These tests are about the SECOND question a verifier has: not "does it verify" but "is it sufficient
for what I am about to conclude". Each one can fail: the mutant that widens a scope, or drops the
declaration, is checked explicitly rather than assumed to be caught.
"""
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _store(tmp_path):
    return Inspeximus(path=str(tmp_path / "s.json"))


def test_the_two_artifacts_declare_different_scopes(tmp_path):
    m = _store(tmp_path)
    m.remember("host is db-old", key="runbook.md")
    ic = m.identifier_contract()
    assert ic["commitment_scope"] == ["key"]
    assert "headroom" in ic["verifies"]
    assert "observation_current" in ic["does_not_verify"]

    w = m.witness()
    assert w["commitment_scope"] == ["store"]
    assert "observation_current" in w["does_not_verify"]


def test_binding_sources_GROWS_the_scope_rather_than_relabelling_it(tmp_path):
    """The witness answers a different question with bind_sources than without, and the artifact
    has to say so -- otherwise the caller cannot tell the two receipts apart after the fact."""
    m = _store(tmp_path)
    src = tmp_path / "runbook.md"
    src.write_bytes(b"host is db-old\n")
    m.remember("host is db-old", key="runbook.md", source={"doc": str(src)})

    plain, bound = m.witness(), m.witness(bind_sources=True)
    assert plain["commitment_scope"] == ["store"]
    assert bound["commitment_scope"] == ["store", "source_digest"]
    assert set(bound["commitment_scope"]) > set(plain["commitment_scope"])
    assert Inspeximus.commitment_supports(plain, "observation_current")["sufficient"] is False
    assert Inspeximus.commitment_supports(bound, "observation_current")["sufficient"] is True


def test_STRATOGAINS_CASE_a_key_scoped_commitment_is_refused_for_a_content_question(tmp_path):
    """The 226-of-634 failure, in miniature: the content moves, the key set does not, and the
    key-scoped commitment is UNCHANGED -- correct, and useless for the question being asked."""
    m = _store(tmp_path)
    src = tmp_path / "runbook.md"
    src.write_bytes(b"host is db-old\n")
    m.remember("host is db-old", key="runbook.md", source={"doc": str(src)})
    before = m.identifier_contract()["population_commitment"]

    src.write_bytes(b"host is db-new\n")          # content moves; the key set does not
    after = m.identifier_contract()["population_commitment"]
    assert before == after, "the key-scoped commitment is stable under a content change -- correct"

    verdict = Inspeximus.commitment_supports(m.identifier_contract(), "observation_current")
    assert verdict["sufficient"] is False
    assert verdict["reason"] == "scope_too_narrow"
    assert verdict["missing"] == ["source_digest"]
    # and the same store CAN answer it, with the right instrument
    assert Inspeximus.commitment_supports(m.witness(bind_sources=True),
                                          "observation_current")["sufficient"] is True


def test_an_undeclared_scope_is_sufficient_for_NOTHING(tmp_path):
    """The case that decides whether this feature is worth having. A report cached before this
    version carries a commitment and no scope, and the tempting reading is 'no declared limits, so
    no limits'. Fail closed, and say WHY so a caller re-mints instead of hunting a bug."""
    cached = {"population_commitment": "deadbeef:13:2.18.0:-"}
    for predicate in ("headroom", "population_identity", "observation_current", "store_unchanged"):
        v = Inspeximus.commitment_supports(cached, predicate)
        assert v["sufficient"] is False
        assert v["reason"] == "undeclared_scope"
        assert "covering nothing" in v["why"]


def test_an_unknown_predicate_is_a_no_not_an_exception(tmp_path):
    m = _store(tmp_path)
    m.remember("x", key="k")
    v = Inspeximus.commitment_supports(m.identifier_contract(), "whether_it_is_tuesday")
    assert v["sufficient"] is False and v["reason"] == "unknown_predicate"
    assert "headroom" in v["known_predicates"]


@pytest.mark.parametrize("predicate,needs", [("headroom", "key"), ("population_identity", "key"),
                                             ("store_unchanged", "store"),
                                             ("observation_current", "source_digest")])
def test_every_predicate_names_a_field_and_the_table_is_not_empty(predicate, needs):
    assert needs in Inspeximus.COMMITMENT_PREDICATES[predicate]


def test_MUTANT_a_widened_scope_would_be_caught(tmp_path):
    """CONTROL. If the helper let a scope claim more than it covers, none of the tests above would
    fail on their own -- so the mutation is made here and asserted to flip the verdict. A gate that
    cannot fail has measured nothing, and this whole feature is a gate."""
    m = _store(tmp_path)
    m.remember("x", key="k")
    honest = m.identifier_contract()
    assert Inspeximus.commitment_supports(honest, "observation_current")["sufficient"] is False

    lying = dict(honest)
    lying["commitment_scope"] = ["key", "source_digest"]        # claims a field it does not cover
    assert Inspeximus.commitment_supports(lying, "observation_current")["sufficient"] is True, (
        "the helper reads the DECLARATION -- it cannot detect a false one, and that is the "
        "documented boundary: this answers whether the artifact CLAIMS enough, not whether the "
        "claim is honest. Verifying the commitment itself is the other question.")

    dropped = {k: v for k, v in honest.items() if k != "commitment_scope"}
    assert Inspeximus.commitment_supports(dropped, "headroom")["reason"] == "undeclared_scope"


def test_the_limits_prose_that_predicted_this_is_still_there(tmp_path):
    """The paragraph stays. It said the right thing and was unqueryable; the fields are additive,
    not a replacement, and deleting the prose would lose the reasoning that produced them."""
    m = _store(tmp_path)
    m.remember("x", key="k")
    limits = " ".join(m.identifier_contract()["limits"])
    assert "would verify clean every time" in limits


def test_a_store_scoped_commitment_subsumes_a_key_scoped_question(tmp_path):
    """A commitment over the whole store necessarily pins the key set inside it. The asymmetry is
    the point: it does NOT run the other way."""
    m = _store(tmp_path)
    m.remember("alpha", key="k1")
    w, ic = m.witness(), m.identifier_contract()

    assert Inspeximus.commitment_supports(w, "population_identity")["sufficient"] is True
    assert Inspeximus.commitment_supports(w, "headroom")["sufficient"] is True
    assert Inspeximus.commitment_supports(w, "population_identity")["covers"] == ["key", "store"]
    # and not the other way: keys say nothing about the rest of the store
    assert Inspeximus.commitment_supports(ic, "store_unchanged")["sufficient"] is False


def test_THE_MEASUREMENT_THE_SUBSUMPTION_RESTS_ON(tmp_path):
    """If a key could leave the store without moving `state_digest()`, the subsumption above would
    be false and a store-scoped receipt would verify clean over a shrunken key set. This asserts the
    property directly, because the first time it was measured the call was wrong -- `forget("beta")`
    passes the text positionally into `ids`, matches nothing, and the digest correctly does not
    move. That reads exactly like a hole in the product."""
    m = _store(tmp_path)
    m.remember("alpha", key="k1")
    rid = m.remember("beta", key="k2")

    before = m.state_digest()
    added = m.remember("gamma", key="k3")
    assert m.state_digest() != before, "adding a key must move the store digest"

    before = m.state_digest()
    res = m.forget(ids=[rid])
    assert res["forgotten"] == 1, "the control: the erasure has to actually happen"
    assert m.state_digest() != before, "a real erasure must move the store digest"

    # the shape that fooled the first attempt: nothing matched, so nothing moved
    before = m.state_digest()
    noop = m.forget(ids=["not-an-id"])
    assert noop["forgotten"] == 0 and m.state_digest() == before
