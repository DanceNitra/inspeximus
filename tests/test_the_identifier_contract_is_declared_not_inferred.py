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


# ───────────────────────────────────────── a zero has two causes, and they render identically
#
# Raised by @Stratogain on anthropics/claude-code#34556, against his own store first and then
# against ours: 13 UUID keys folded to 8 hex characters collide with probability ~1e-8, so the
# zero he measured was the ABSENCE OF A SIGNAL. `invertible_on_this_store` was never false there --
# it was true, and about to stop being true without saying so. That is our own "a frozen integer
# about a growing population is a claim with an expiry date nobody printed on it", turned around.

def _uuidish(n):
    import uuid
    return [uuid.uuid4().hex[:8] for _ in range(n)]


def test_a_zero_on_a_population_too_small_to_collide_is_not_a_clean_bill_of_health():
    """The boolean stays true, because it is true. The verdict is what a reader should act on."""
    m = _store(_uuidish(13)).identifier_contract()["measured"]["prefix_8"]
    assert m["keys_that_would_be_lost"] == 0
    assert m["invertible_on_this_store"] is True, "on the keys present, this fold loses nothing"
    assert m["verdict"] == "NOT_YET_MEASURABLE", \
        "13 keys against an 8-character fold cannot demonstrate anything about the fold"
    assert m["threshold_population"] > 13

    # MUST-FAIL CONTROL: the pre-fix rule derived everything from `lost`, so it would have called
    # this store measured-and-clean. If a refactor ever collapses the two, this fixture says so.
    old_rule = "COST_MEASURED" if m["keys_that_would_be_lost"] else "ZERO_AT_SCALE"
    assert old_rule != m["verdict"], \
        "the fixture stopped discriminating: it no longer separates the old rule from the new one"


def test_control_a_store_past_its_threshold_does_claim_scale():
    """THE OTHER DIRECTION. Without it, a checker that answered NOT_YET_MEASURABLE to everything
    would pass the test above, which is the failure mode that test is about."""
    import itertools
    keys = ["".join(b) for b in itertools.islice(itertools.product("01", repeat=8), 40)]
    m = _store(keys).identifier_contract()["measured"]["prefix_8"]
    assert m["keys_that_would_be_lost"] == 0
    assert m["positions_saturated"] == 0, "a 2-character alphabet is resolved by 40 keys"
    assert m["verdict"] == "ZERO_AT_SCALE"
    assert m["threshold_population"] <= 40


def test_control_a_colliding_store_still_reports_the_cost():
    """The new verdict must not swallow the measurement it was added beside."""
    m = _store(["src/agora/mod_%03d.py" % i for i in range(60)]).identifier_contract()["measured"]["prefix_8"]
    assert m["verdict"] == "COST_MEASURED"
    assert m["keys_that_would_be_lost"] == 59 and m["invertible_on_this_store"] is False


def test_the_threshold_reproduces_the_analytic_birthday_bound_on_hex_keys():
    """POSITIVE CONTROL on the estimator itself. The space is measured from the keys rather than
    assumed from an alphabet, precisely so it can handle file paths -- but where the closed form
    does apply, the measurement has to agree with it or it is measuring something else."""
    import math
    import uuid

    from inspeximus.core import _prefix_collision_threshold
    keys = [uuid.uuid4().hex for _ in range(4000)]
    for length in (4, 6, 8):
        got = _prefix_collision_threshold(keys, length)
        want = math.ceil(math.sqrt(2 * (16 ** length) * math.log(1 / 0.99)))
        assert abs(got - want) / want < 0.02, \
            f"prefix_{length}: measured {got} against analytic {want}"


def test_saturated_positions_block_a_claim_of_scale_even_above_the_threshold():
    """Three short words sit above their own estimated threshold, and the estimate is the sample
    size in disguise: every key differs at position 0, so the store cannot tell an alphabet of
    three from an alphabet of thirty. It must not read as scale."""
    m = _store(["alpha", "beta", "gamma"]).identifier_contract()["measured"]["prefix_8"]
    assert m["positions_saturated"] > 0
    assert m["threshold_population"] <= 3, "the fixture must be ABOVE its threshold, or it proves nothing"
    assert m["verdict"] == "NOT_YET_MEASURABLE"


def test_the_headroom_is_measured_rather_than_modelled():
    """The threshold answers 'how many more keys', which needs a model of where keys come from.
    This answers 'how many fewer characters', which needs nothing but the store in front of you."""
    # 'abd1x'/'abd2y' must separate at 4 so that 3 is the LONGEST colliding length, not merely a
    # colliding one -- prefix collisions are monotone in length and the report gives the edge.
    keys = ["abc" + s for s in ("Xq1", "Yq2", "Zq3")] + ["abd1x", "abd2y"]
    m = _store(keys).identifier_contract()["measured"]["prefix_8"]
    assert m["keys_that_would_be_lost"] == 0, "distinct at 8 characters"
    assert m["collides_at_length"] == 3, "but 'abc'/'abd' merge at 3, and nothing merges at 4"
    assert m["headroom_chars"] == 5
    # THE TWO-NUMBERS RULE: these two sum to the fold length, so a reader can confuse them and a
    # mutant returning one for the other must die. No fixture here may leave them equal.
    assert m["collides_at_length"] != m["headroom_chars"]


def test_a_fold_with_no_threshold_model_says_so_instead_of_implying_safety():
    """Casefold has no length to reason about, so there is no population at which its zero starts
    to mean something. Claiming scale there would be the same vacuity one level over."""
    m = _store(["alpha", "beta", "gamma"]).identifier_contract()["measured"]["casefold"]
    assert m["verdict"] == "ZERO_NO_THRESHOLD_MODEL"
    assert "threshold_population" not in m
    assert m["invertible_on_this_store"] is True, "still a true statement about the keys present"


# ───────────────────────────────────── at-scale is not the same as safe
#
# Raised by @safal207 on anthropics/claude-code#34556: on a heavily prefixed population the
# population threshold says ZERO_AT_SCALE while one character less would collapse the store. Measured
# on both of his examples: at the fold he names -- one past the shared prefix -- the keys DO collide,
# so COST_MEASURED fires and the model never gets to give false comfort. One character further out he
# is exactly right, and that cell is identifiable without any statistics: it is the shortest fold
# that does not merge, so its headroom is 1 by definition.

def _prefixed():
    """A store whose 12-character fold is the SHORTEST that does not merge, built deterministically.

    The first version drew random suffixes and relied on a birthday collision landing between 11 and
    12 characters -- which it does at 4,000 keys and does not at 400, so the fixture's meaning
    depended on its size. Here 20 groups of 26 give exactly 20 distinct 11-prefixes and 520 distinct
    12-prefixes, so the cliff is at 12 by construction rather than by luck.
    """
    import string
    return [("grp%08d" % g) + c + "-tail" for g in range(20) for c in string.ascii_lowercase]


def test_a_fold_at_the_cliff_edge_is_named_even_though_it_does_not_merge():
    """ZERO_AT_SCALE with one character of headroom is the shortest safe fold on the store. The
    verdict is true and the warning is what a reader needs beside it."""
    c = _store(_prefixed()).identifier_contract()
    m = c["measured"]["prefix_12"]
    assert m["verdict"] == "ZERO_AT_SCALE" and m["keys_that_would_be_lost"] == 0
    assert m["headroom_chars"] == 1
    assert c["at_cliff_edge"] == ["prefix_12"]
    assert any("CLIFF EDGE" in x for x in c["limits"]), c["limits"]


def test_a_colliding_fold_with_the_same_headroom_is_NOT_called_a_cliff():
    """MUST-FAIL CONTROL, and the fixture is the same store. `prefix_8` also has one character of
    headroom, but it MERGES 400-odd keys -- it is not a cliff edge, it is over the edge, and the
    verdict already says so. A check keyed on headroom alone would flag it, so this is the case that
    separates 'reads the verdict' from 'reads one number'."""
    c = _store(_prefixed()).identifier_contract()
    assert c["measured"]["prefix_8"]["verdict"] == "COST_MEASURED"
    assert c["measured"]["prefix_8"]["headroom_chars"] == 1, \
        "the fixture stopped being discriminating: both folds must share the headroom"
    assert "prefix_8" not in c["at_cliff_edge"]


def test_control_an_ordinary_store_gets_no_cliff_warning():
    """Without this, a checker that warned on everything would pass the test above."""
    c = _store(["alpha", "beta", "gamma"]).identifier_contract()
    assert c["at_cliff_edge"] == []
    assert not any("CLIFF EDGE" in x for x in c["limits"])


# ───────────────────────────────────────── #34556: the two findings from the thread
#
# @Stratogain measured his own store and found that `at_cliff_edge` cannot fire on path-shaped keys:
# the conjunction ZERO_AT_SCALE && headroom == 1 is not false there but UNSATISFIABLE, because the
# threshold at a 149-character fold outruns any population. He also showed a second, unrelated way
# the field goes quiet -- positions_saturated on a tiny store -- with nothing in the output telling
# the two apart. @safal207 (CML #311) froze five ways a correct measurement goes stale before use;
# four of them landed on this contract, the worst being that {A,B,C} and {A,B,D} produced a
# byte-identical report because the only population fact carried was a count.


def _deep_paths(n=200, depth=148):
    """A store whose cliff is set by its deepest directory rather than its common root.

    THE FIXTURE HAS TO LAND EXACTLY. A first version made the shared directory 149 characters and
    gave two files names beginning with the same letter, so the four keys still merged at fold 149
    and the test failed against correct code. The prefix is now exactly `depth` characters and the
    four file names differ at their first character, so character depth+1 is the first that tells
    them apart."""
    base = "c:/a/" + "d" * max(0, depth - 6) + "/"
    assert len(base) == depth, "the shared prefix must be exactly %d chars, got %d" % (depth, len(base))
    out = [base + name for name in ("a.ts", "b.ts", "c.ts", "e.ts")]
    out += ["c:/%03d/%03d.py" % (i, i) for i in range(n)]
    return sorted(set(out))


def test_the_caller_can_name_the_fold_their_store_actually_uses():
    """8 and 12 are defaults, not a claim about anyone's keys. A cliff at 148 is outside the
    instrument until the caller says which fold they fold on."""
    keys = _deep_paths()
    c = _store(keys).identifier_contract(prefix_folds=[149])
    assert "prefix_149" in c["measured"], sorted(c["measured"])
    assert c["measured"]["prefix_149"]["keys_that_would_be_lost"] == 0
    assert c["measured"]["prefix_149"]["headroom_chars"] == 1
    assert "prefix_149" in c["cliff"]["measured_folds_at_the_cliff"]


def test_the_cliff_is_reported_even_when_no_measured_fold_is_near_it():
    """The store's cliff is a property of its keys, not of which folds we happened to measure."""
    c = _store(_deep_paths()).identifier_contract()
    assert c["at_cliff_edge"] == [], "the old field keeps its old, narrow meaning"
    assert c["cliff"]["collides_at_length"] == 148
    assert c["cliff"]["first_clean_fold"] == 149


def test_the_silence_carries_its_reason_and_the_reasons_differ():
    """His sharpest point: two empty fields, opposite meanings. On paths the threshold at the clean
    fold is unreachable; on a store no fold merges, there is simply no cliff. Same `[]` before."""
    paths = _store(_deep_paths()).identifier_contract()
    tiny = _store(["aaa11111", "bbb22222", "ccc33333"]).identifier_contract()
    assert paths["at_cliff_edge"] == tiny["at_cliff_edge"] == []
    assert paths["cliff"]["why_at_cliff_edge_is_silent"] == "threshold_unreachable_at_that_fold"
    assert tiny["cliff"]["why_at_cliff_edge_is_silent"] == "no_fold_merges_these_keys"
    assert paths["cliff"]["why_at_cliff_edge_is_silent"] != tiny["cliff"]["why_at_cliff_edge_is_silent"]


def test_cliff_is_always_a_dict_never_a_falsy_sentinel():
    """MUST-FAIL CONTROL for the bug class this very function already had once. A caller writing
    `if report["cliff"]:` must not conflate 'no cliff' with 'not examined'."""
    for keys in (["alpha", "beta", "gamma"], _deep_paths()):
        c = _store(keys).identifier_contract()
        assert isinstance(c["cliff"], dict), type(c["cliff"])
        assert c["cliff"]["why_at_cliff_edge_is_silent"] is not None or c["at_cliff_edge"]


def test_when_the_warning_should_fire_the_reason_is_None():
    """The reason field is not decoration: on the fixture that DOES trip the warning it must be
    None, or a consumer could not tell 'quiet for a reason' from 'loud'."""
    c = _store(_prefixed()).identifier_contract()
    if c["at_cliff_edge"]:
        assert c["cliff"]["why_at_cliff_edge_is_silent"] is None, c["cliff"]


def test_population_commitment_separates_two_stores_of_the_same_size():
    """@safal207's class 1, which landed hardest: {A,B,C} and {A,B,D} are three keys either way."""
    a = _store(["aaa11111", "bbb22222", "ccc33333"]).identifier_contract()
    b = _store(["aaa11111", "bbb22222", "ddd44444"]).identifier_contract()
    assert a["keys"] == b["keys"] == 3, "the fixture must keep the counts equal or it proves nothing"
    assert a["population_commitment"] != b["population_commitment"]


def test_population_commitment_is_stable_for_the_same_population():
    """MUST-FAIL CONTROL: a commitment that changed on every read would pass the test above and be
    useless, because every cached report would look stale."""
    keys = ["aaa11111", "bbb22222", "ccc33333"]
    assert (_store(keys).identifier_contract()["population_commitment"]
            == _store(list(reversed(keys))).identifier_contract()["population_commitment"])


def test_population_commitment_does_not_cross_tenants():
    """His class 5. Identical keys in two scopes are not the same population to a consumer."""
    keys = ["aaa11111", "bbb22222", "ccc33333"]
    one = _store(keys, tenant="tenant-1").identifier_contract()
    two = _store(keys, tenant="tenant-2").identifier_contract()
    assert one["keys"] == two["keys"]
    assert one["population_commitment"] != two["population_commitment"]


def test_the_limits_name_what_the_commitment_still_does_not_cover():
    """Two of his five are NOT fixed here, and saying so is the point: a deleted key takes the
    evidence of its own collision with it, and nothing binds the writer policy of a single record."""
    c = _store(["aaa11111", "bbb22222"]).identifier_contract()
    joined = " ".join(c["limits"])
    assert "cannot tell you what it lost" in joined, c["limits"]
    assert "does NOT bind the writer policy" in joined, c["limits"]
