"""A store outlives its writer, and the inheritor cannot run anyone's conformance suite.

WHERE THIS COMES FROM. Four projects spent a day on anthropics/claude-code#34556 naming provenance
defect classes, and every one of us reasoned at WRITE time about code that currently runs. A review
pass asked who was missing and the answer was the party who holds the store six months later, with
the writing version gone and the maintainer possibly too. For them the deformation is already in the
bytes; no test can be run against a system that no longer exists.

What that party needs is a different partition from the one detection uses. At remediation time it
is INVERTIBLE versus COLLAPSING: an injective deformation is a backfill job, while a fold mapping two
distinct keys onto one cannot be undone, because you cannot un-merge. And they cannot tell a
DELIBERATE fold (case-insensitive lookup, IdP tenant ids, NFC on purpose) from an accident, because
the identifier contract is implicit -- measured on our own decision store: 11,438 records and not one
field declaring any policy.

So the contract is declared by the writer AND the cost of each fold is measured against the store's
own keys, so the claim and the evidence sit side by side and a reader can compare them.
"""
from __future__ import annotations

import os
import tempfile
import unicodedata

import pytest

from inspeximus import Inspeximus


def _store(keys, **kw):
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), **kw)
    for i, k in enumerate(keys):
        ix.remember(f"value {i}", key=k, object=str(i))
    ix.flush()
    return ix


# ───────────────────────────────────────────── the declaration
def test_the_writer_states_its_policy_rather_than_leaving_it_to_inference():
    """Data cannot distinguish a policy from an accident. Only the writer can say which it was."""
    c = _store(["a", "b"]).identifier_contract()
    d = c["declared"]
    assert "byte-exact" in d["keys_stored"] and d["unicode_normalisation"] is None
    assert "case-sensitive" in d["lookup"]
    assert d["declared_by_version"], "a contract with no version cannot be attributed to a writer"


def test_the_declaration_carries_what_it_cannot_speak_for():
    """A contract that omits its own scope is the thing this feature exists to replace."""
    c = _store(["a"]).identifier_contract()
    joined = " ".join(c["limits"])
    assert "not for the version that wrote" in joined or "not the version that wrote" in joined
    assert "absence of merging is not proof" in joined


# ───────────────────────────────────────────── invertible vs collapsing, BOTH directions
def test_a_collapsing_fold_is_reported_as_not_invertible():
    c = _store(["Acme-Tenant", "acme-tenant"]).identifier_contract()
    m = c["measured"]["casefold"]
    assert m["invertible_on_this_store"] is False
    assert m["keys_that_would_be_lost"] == 1 and m["groups_that_would_merge"] == 1
    assert len(m["example"]) == 2, "a lossy fold must name the keys it would merge"


def test_groups_and_lost_keys_are_different_numbers_and_must_diverge():
    """MUTANT-KILLER, added after a red-team pass found every other fixture in this file used exactly
    one two-key merge -- so `groups_that_would_merge` and `keys_that_would_be_lost` were numerically
    identical (1 and 1) everywhere, and a mutation replacing the second with the first PASSED ALL
    EIGHT TESTS. The suite reached the code and never reached the input region where the bug shows.

    That is this file's own subject matter committed inside this file: Reachability is the first RIPR
    condition (Ammann & Offutt, "Introduction to Software Testing" 2nd ed., 2017) and a cover on the
    antecedent does not supply it -- the antecedent WAS covered, the discriminating input was not.

    Real data diverges by more than 2x: on our coding store `prefix_8` merges 599 groups and loses
    1,373 keys. So the fixture below is the ordinary case, not a corner one."""
    # one group of THREE keys: 1 group, 2 keys lost -- the two fields must not be equal
    c = _store(["prefix-x-alpha", "prefix-x-beta", "prefix-x-gamma", "different"]).identifier_contract()
    m = c["measured"]["prefix_8"]
    assert m["groups_that_would_merge"] == 1
    assert m["keys_that_would_be_lost"] == 2
    assert m["groups_that_would_merge"] != m["keys_that_would_be_lost"], \
        "a store where these two coincide cannot tell the two definitions apart"


def test_lost_keys_sum_across_several_groups():
    """The other half of the same hole: several groups, each losing a different amount. A mutant
    that returned `max` or the first group's loss instead of the sum survives the test above."""
    c = _store(["aaaaaaaa-1", "aaaaaaaa-2", "aaaaaaaa-3",     # 1 group, loses 2
                "bbbbbbbb-1", "bbbbbbbb-2",                    # 1 group, loses 1
                "solitary-key"]).identifier_contract()
    m = c["measured"]["prefix_8"]
    assert m["groups_that_would_merge"] == 2
    assert m["keys_that_would_be_lost"] == 3, "losses must SUM over groups, not max or first"


def test_two_rows_sharing_one_key_are_not_a_collision():
    """The same key written twice is one identifier, not two colliding ones. Without this, ordinary
    supersession would inflate every fold's cost and the whole report would read as alarming."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    ix.remember("first", key="same-key", object="1")
    ix.remember("second", key="same-key", object="2")
    ix.flush()
    m = ix.identifier_contract()["measured"]["casefold"]
    assert m["keys_that_would_be_lost"] == 0 and m["invertible_on_this_store"] is True


def test_control_the_same_fold_is_invertible_on_a_store_that_does_not_collide():
    """THE OTHER DIRECTION, and the reason this is a property of the DATA rather than of the fold.
    Without it, a checker that always answered 'not invertible' would pass the test above."""
    c = _store(["alpha", "beta", "gamma"]).identifier_contract()
    assert c["measured"]["casefold"]["invertible_on_this_store"] is True
    assert c["measured"]["casefold"]["keys_that_would_be_lost"] == 0


def test_a_prefix_fold_is_measured_because_that_is_the_one_that_bit_a_peer():
    """A hook on anthropics/claude-code#34556 stored a truncated session id and compared the full
    one. No handle here on purpose: two of our own files credited that case to two different people,
    which is its own small lesson about restating someone else's bug in your words. On our coding
    store an 8-character prefix fold would lose 1,373 of 11,501 keys, which makes it concrete."""
    c = _store(["session-abcdefgh-alpha", "session-abcdefgh-omega"]).identifier_contract()
    assert c["measured"]["prefix_8"]["invertible_on_this_store"] is False
    assert c["measured"]["prefix_12"]["keys_that_would_be_lost"] >= 1


def test_unicode_normalisation_is_measured_separately_from_case():
    """NFC and NFD are the same word to a human and different bytes to a store — the IDNA2003/2008
    deviation class. It must not be folded into the casefold answer."""
    nfd = unicodedata.normalize("NFD", "sedácia")
    nfc = unicodedata.normalize("NFC", "sedácia")
    c = _store([nfd, nfc]).identifier_contract()
    assert c["non_ascii_keys"] == 2 and c["mixed_normalisation"] == 1
    assert c["measured"]["unicode_nfc"]["invertible_on_this_store"] is False
    assert c["measured"]["casefold"]["invertible_on_this_store"] is True, \
        "case and normalisation are different questions and must not share an answer"


# ───────────────────────────────────────────── it is a per-tenant answer
def test_a_tenant_sees_only_its_own_identifiers():
    """The report names keys, and a key is exactly what must not cross a tenant boundary."""
    root = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)
    root.for_tenant("acme").remember("x", key="acme-only", object="1")
    root.for_tenant("globex").remember("y", key="globex-only", object="2")
    root.flush()
    c = root.for_tenant("acme").identifier_contract()
    assert c["keys"] == 1
    assert "globex-only" not in str(c)


def test_control_an_empty_store_does_not_claim_invertibility_it_cannot_have():
    """Nothing to fold is not proof that folding is safe. It reports zero keys and the reader can
    see the denominator, rather than a cheerful all-invertible verdict over nothing."""
    c = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json")).identifier_contract()
    assert c["keys"] == 0
    assert all(m["keys_that_would_be_lost"] == 0 for m in c["measured"].values())
    assert any("only surviving keys" in x for x in c["limits"])
