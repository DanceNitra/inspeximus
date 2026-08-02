# -*- coding: utf-8 -*-
"""Does the deterministic, zero-LLM keyer hold ONE key across a real conversational correction chain?

The product's central promise is that corrections stick, and supersession is KEYED, so the promise is only
as good as the key. Measured on benchmarks/chain_binding/ before this work: 2 of 15 chains bound, which is
the advertised behaviour not firing at all on the input the product is sold for. After: 9 of 15, with the
negative control at 0 false binds.

Both directions are asserted here, and the second is the one that gives the first any meaning: a keyer that
binds everything scores 15/15 and silently destroys unrelated records. So every number below comes in a
pair, and the unsolved chains are pinned as unsolved -- a suite that cannot tell "the fix works" from "the
case never arises" has measured nothing.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "chain_binding"))

import pytest                                                        # noqa: E402

from fixture import CHAINS, KNOWN_UNFIXED, NEGATIVES, PROSE          # noqa: E402
from inspeximus.core import Inspeximus, derive_key, regex_extractor   # noqa: E402

# The chains that bind TODAY, named individually so a regression says which one broke rather than only
# that a count moved. The rest are pinned as unsolved further down, with the reason each one is unsolved.
BINDING = {"employer", "city", "manager", "team", "email", "timezone", "phone", "release", "readme_title"}
# Unsolved, and WHY. Four of these need world knowledge ("a Principal Engineer" is a *title*), which no
# deterministic keyer reaches without an ontology or a model; two are surface problems that are genuinely
# undecidable without a lexicon (where does the head start in an English noun compound?).
UNSOLVED = {"title", "atlas_deadline", "language", "dan_role", "analytics_store", "diet"}


# An empty sweep satisfies every "nothing went wrong" assertion below, so the sizes are pinned first.
assert len(CHAINS) == 15 and len(NEGATIVES) == 18 and len(PROSE) == 60 and len(KNOWN_UNFIXED) == 2
assert BINDING | UNSOLVED == {c[0] for c in CHAINS} and not (BINDING & UNSOLVED)

# NOTE ON MODES: everything here reads `m.items[*]["status"]`, which is what supersession writes. Nothing
# goes through `recall()`, so there is no embedder and no lexical/semantic mode that could silently be the
# only path exercised. Binding is a WRITE-path property and is measured on the write path.


def _store(keyer=regex_extractor):
    m = Inspeximus(path=None)
    m.extractor = keyer
    return m


def _bind_count(keyer):
    """Chains that collapse to exactly one active record, under an arbitrary keyer."""
    n = 0
    for _cid, _shape, turns, _final in CHAINS:
        m = _store(keyer)
        for t in turns:
            m.remember(t)
        n += sum(1 for r in m.items if r.get("status") == "active") == 1
    return n


def _false_bind_count(keyer):
    """Unrelated pairs where one record retired the other, under an arbitrary keyer."""
    n = 0
    for _pid, _why, a, b in NEGATIVES:
        m = _store(keyer)
        m.remember(a)
        m.remember(b)
        n += sum(1 for r in m.items if r.get("status") == "active") < 2
    return n


def test_both_controls_can_actually_fail():
    """The check on the checks. A control that cannot fail has measured nothing, so each direction is
    driven to its degenerate extreme and must register it:

      * a keyer that binds NOTHING scores 0 chains  -> the bind measurement can fail;
      * a keyer that binds EVERYTHING scores ALL 15 chains AND all 18 false binds -> which is exactly why
        the bind rate alone is not evidence, and why the negative control is not optional.

    The shipped keyer must sit strictly inside both extremes.
    """
    bind_nothing = lambda _t: None                       # noqa: E731
    bind_everything = lambda t: ("one-key-for-everything", t)   # noqa: E731

    assert _bind_count(bind_nothing) == 0
    assert _false_bind_count(bind_nothing) == 0

    assert _bind_count(bind_everything) == len(CHAINS) == 15, (
        "a keyer that binds everything must score a PERFECT bind rate -- if it does not, the bind "
        "measurement is not measuring binding")
    assert _false_bind_count(bind_everything) == len(NEGATIVES) == 18, (
        "...and must be caught by every negative pair; that is the only thing separating the two")

    assert _bind_count(regex_extractor) == 9
    assert _false_bind_count(regex_extractor) == 0


def _keys(turns):
    return [(regex_extractor(t) or (None,))[0] for t in turns]


def _ingest(turns):
    m = _store()
    for t in turns:
        m.remember(t)
    return [r for r in m.items if r.get("status") == "active"]


# ── the promise: a correction chain collapses to one record holding the final value ──────────────────
@pytest.mark.parametrize("cid,shape,turns,final",
                         [c for c in CHAINS if c[0] in BINDING],
                         ids=[c[0] for c in CHAINS if c[0] in BINDING])
def test_a_correction_chain_collapses_to_the_final_value(cid, shape, turns, final):
    ks = _keys(turns)
    assert ks[0] is not None, f"{cid}: the opening statement was never keyed ({shape})"
    assert all(k == ks[0] for k in ks), f"{cid}: keys diverge across the chain -> {ks} ({shape})"
    active = _ingest(turns)
    assert len(active) == 1, f"{cid}: {len(active)} records survive, so the correction did not stick"
    assert final.lower() in active[0]["text"].lower(), f"{cid}: the surviving record is not the correction"


def test_the_measured_bind_rate_does_not_regress():
    bound = sum(len(_ingest(t)) == 1 for _cid, _s, t, _f in CHAINS)
    assert bound == len(BINDING) == 9, (
        f"bind rate moved: {bound}/{len(CHAINS)} chains collapse to one record, expected {len(BINDING)}. "
        f"Up is good news that belongs in BINDING; down is a regression.")


@pytest.mark.parametrize("cid", sorted(UNSOLVED))
def test_an_unsolved_chain_is_still_unsolved(cid):
    """The control that keeps the measurement honest. If one of these starts binding, the fixture has
    stopped reproducing the defect (or the keyer got better) -- either way the number above is stale and
    must be re-derived, not quietly inherited."""
    turns = next(t for c, _s, t, _f in CHAINS if c == cid)
    assert len(_ingest(turns)) > 1, f"{cid} now binds; move it into BINDING and re-measure"


# ── the control: unrelated facts must never share a key ──────────────────────────────────────────────
@pytest.mark.parametrize("pid,why,a,b", NEGATIVES, ids=[n[0] for n in NEGATIVES])
def test_unrelated_statements_never_bind(pid, why, a, b):
    ka = (regex_extractor(a) or (None,))[0]
    kb = (regex_extractor(b) or (None,))[0]
    assert not (ka and ka == kb), f"{pid}: both keyed on {ka!r} -- {why}"
    m = _store()
    m.remember(a)
    m.remember(b)
    active = [r for r in m.items if r.get("status") == "active"]
    assert len(active) == 2, f"{pid}: one record retired the other -- {why}"


def test_a_non_referring_subject_never_keys_onto_the_users_own_facts():
    """The data-loss hazard this guard exists for: an expletive subject identifies nothing, so its key
    collides across unrelated sentences and supersession retires live records."""
    for text in ("It is important to ship on Friday",
                 "There is a hard deadline on Friday",
                 "That is a memorable number",
                 "These are the options we discussed",
                 "What is the deadline?"):
        assert regex_extractor(text) is None, text
    m = _store()
    m.remember("my deadline is Friday")
    m.remember("It is important to ship on Friday")
    assert [r["status"] for r in m.items] == ["active", "active"]


def test_a_quantifier_or_demonstrative_subject_is_rejected():
    for text in ("Both approaches are defensible",
                 "Some of the tests are flaky under load",
                 "One thing I noticed is that the cache never expires",
                 "This kind of thing is exactly why we added the linter"):
        assert regex_extractor(text) is None, text


# ── the anti-greed control: conservative on prose, or the guard above is worthless ───────────────────
def test_non_declarative_prose_stays_conservative_and_retires_nothing():
    keyed = [s for s in PROSE if regex_extractor(s)]
    assert len(keyed) <= 4, f"keying got greedier on prose: {len(keyed)}/{len(PROSE)} -> {keyed}"
    m = _store()
    for s in PROSE:
        m.remember(s)
    assert len(m.items) == len(PROSE) == 60, "the sweep must actually have written every sentence"
    retired = [r["text"] for r in m.items if r.get("status") != "active"]
    assert retired == [], f"prose retired records that are about nothing in common: {retired}"


# ── the individual mechanisms, each with the sentence that motivated it ──────────────────────────────
def test_a_leading_clause_no_longer_blocks_the_key():
    """Every pattern is anchored at ^ and the subject class has no comma, so a lead-in used to kill it."""
    assert derive_key("Dana left, so my manager is Priya now") == ("self::manager", "Priya")
    assert derive_key("we shipped v2.2 yesterday, so the current release is v2.2") == ("release", "v2.2")


def test_discourse_markers_are_stripped_from_the_subject_side():
    for text in ("correction: my title is Director", "update: my title is Director",
                 "actually my title is Director", "fyi my title is Director",
                 "so my title is Director"):
        assert derive_key(text) == ("self::title", "Director"), text


def test_a_marker_that_is_also_a_noun_needs_punctuation_to_be_stripped():
    """'correction'/'update'/'note' are ordinary nouns too; stripping them unconditionally rewrites the
    subject of a sentence that is genuinely about them."""
    assert derive_key("note taking is my hobby") == ("note taking", "my hobby")


def test_a_trailing_time_adverbial_is_tense_not_value():
    assert derive_key("my manager is Priya now") == derive_key("my manager is Priya")


def test_stripping_a_trailing_adverbial_never_eats_the_whole_object():
    """'today' is the value here, not the tense -- the stripped candidate fails to parse and the
    unstripped one is used, so the fact survives."""
    assert derive_key("the meeting is today") == ("meeting", "today")


def test_first_person_is_one_referent_across_surface_forms():
    assert derive_key("my employer is Initech")[0] == "self::employer"
    assert derive_key("I work at Globex")[0] == "self::employer"
    assert derive_key("I live in Lisbon")[0] == "self::residence"


def test_a_contracted_pronoun_is_still_a_pronoun():
    """Shipped behaviour keyed BOTH of these on 'i’m', so two unrelated facts about the user retired
    each other. That was a live data-loss path, not a missed bind."""
    a = regex_extractor("I'm now in the PST timezone")
    b = regex_extractor("I'm now the on-call engineer")
    assert a == ("self::timezone", "PST")
    assert b is None
    assert not (a and b and a[0] == b[0])


def test_a_possessed_subject_nests_instead_of_becoming_a_relation_on_the_user():
    """If 'my wife works at Acme' keyed as self::wife it would retire 'my wife is Sarah'."""
    assert derive_key("my wife is Sarah") == ("self::wife", "Sarah")
    assert derive_key("my wife works at Acme Corp") == ("self.wife::employer", "Acme Corp")


def test_a_current_marking_modifier_folds_away_but_a_historical_one_does_not():
    """README's own failing example is the first line. The second is the asymmetry that makes the modifier
    list a closed list: folding 'former' in would let a new job destroy the record of the old one."""
    assert derive_key("my official title was Junior Data Analyst")[0] == "self::title"
    assert derive_key("so my current title is Data Analyst")[0] == "self::title"
    assert derive_key("my former employer is Acme Corp")[0] == "self::former employer"
    assert derive_key("my employer is Globex")[0] == "self::employer"


def test_the_head_noun_of_the_complement_carries_the_relation():
    assert derive_key("I'm on the Payments team") == ("self::team", "Payments")
    assert derive_key("I moved to the Platform team last week") == ("self::team", "Platform")


def test_an_evaluative_adjective_is_a_comment_not_a_value():
    """'the PST timezone' and 'the wrong timezone' are the same shape; keying the second would retire the
    fact it complains about. Closed list, so an adjective outside it still binds -- that residual is real."""
    assert derive_key("I'm in the wrong timezone") is None
    assert derive_key("I'm in the CET timezone") == ("self::timezone", "CET")


def test_two_clauses_with_different_keys_are_ambiguous_and_decline():
    """Clause splitting only runs when the whole sentence fails to parse, and when it does run, two clauses
    disagreeing means the sentence states two facts. A mis-derived key mis-supersedes, so it declines."""
    assert derive_key("Dana left, so my manager is Priya, and my title is Director") is None
    # ...while two clauses AGREEING is not ambiguous and still binds.
    assert derive_key("Dana left, so my manager is Priya now") == ("self::manager", "Priya")


def test_the_whole_sentence_is_preferred_over_its_clauses():
    """The full string is the most faithful reading, so a comma INSIDE a value does not split the fact."""
    assert derive_key("my email signature is 'Best, Dan'")[0] == "self::email signature"


# ── the boundary, asserted so that crossing it is a deliberate act ───────────────────────────────────
def test_a_statement_that_names_only_the_value_is_not_keyed():
    """These need world knowledge -- that a Principal Engineer is a *title*, that vegan is a *diet*, that
    an engineering manager is a *role*. A deterministic keyer cannot reach it, and guessing would retire a
    live record. Pass key= explicitly, or plug make_llm_extractor, when you need them bound."""
    for text in ("actually I'm a Principal Engineer now", "I'm vegan now", "I'm vegetarian",
                 "actually I prefer Rust these days"):
        assert regex_extractor(text) is None, text
    # "Dan is now an engineering manager" DOES key -- on the bare subject `dan`, not on `dan::role` -- so
    # it still fails to join the chain it belongs to, for the same reason: nothing in the sentence says
    # that an engineering manager is a *role*.
    assert regex_extractor("Dan is now an engineering manager")[0] == "dan"
    assert regex_extractor("Dan's role is director")[0] == "dan::role"


@pytest.mark.parametrize("pid,why,a,b", KNOWN_UNFIXED, ids=[k[0] for k in KNOWN_UNFIXED])
def test_the_bare_copula_hazard_is_pinned_as_unchanged(pid, why, a, b):
    """Pre-existing and deliberately untouched: "X is Y" keys on the subject alone, which is what keys the
    README's "The API rate limit is 500 rps", and the cost is that two attributes of one entity collide.
    Pinned rather than hidden -- if this ever stops binding, the bare-copula contract changed and every
    caller relying on it needs to hear about it."""
    ka = (regex_extractor(a) or (None,))[0]
    kb = (regex_extractor(b) or (None,))[0]
    assert ka and ka == kb, f"{pid}: the bare-copula contract changed ({ka!r} vs {kb!r}) -- {why}"


def test_two_bare_self_predications_do_not_collide():
    m = _store()
    m.remember("I'm vegetarian")
    m.remember("I'm exhausted")
    assert [r["status"] for r in m.items] == ["active", "active"]


def test_whitespace_is_collapsed_before_matching():
    """Deterministic half of the backtracking fix: runs of whitespace never distinguish two facts."""
    assert derive_key("my  title\tis   Director") == derive_key("my title is Director")
    assert derive_key("   my title is Director   ") == ("self::title", "Director")


def test_a_run_of_spaces_does_not_blow_up_the_write_path():
    """The subject class contains a literal space, so a run of spaces used to be a run of equally valid
    split points and the cost went quadratic -- 0.7 ms at 100 spaces, 46.9 ms at 800, on `remember()`.
    The bound is deliberately loose (the defect exceeded it by ~12x at this size) so machine noise cannot
    flip it, while a return of the quadratic term still fails loudly."""
    import time
    t0 = time.perf_counter()
    derive_key(" " * 4000)
    assert (time.perf_counter() - t0) < 0.1


# ── contract ────────────────────────────────────────────────────────────────────────────────────────
def test_regex_extractor_is_the_public_alias_of_derive_key():
    for text in ("my title is Director", "nonsense", "", "I'm on the Payments team"):
        assert regex_extractor(text) == derive_key(text)


def test_the_keyer_is_fail_open_on_junk():
    for junk in (None, "", 17, [], {"a": 1}, "   ", "?" * 500):
        assert derive_key(junk) is None or isinstance(derive_key(junk), tuple)


def test_the_write_path_still_stores_the_record_when_no_key_is_derived():
    m = _store()
    m.remember("Anyway, the point is that the retry budget is too small.")
    m.remember("I'm vegetarian")
    assert len(m.items) == 2 and all(r["status"] == "active" for r in m.items)
