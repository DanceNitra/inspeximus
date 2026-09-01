# -*- coding: utf-8 -*-
"""check_conflict's keyed path: one normaliser, and no silent clean.

TWO DEFECTS, both measured on 2.24.1 before this file existed.

1. The keyed comparison was `r["object"] != object` on raw strings, while supersession compared
   `_obj_sig(r)`, which lowercases and collapses punctuation. So the same store could supersede two
   records as the same value and simultaneously report them as a contradiction. Measured: `Senior
   Data Analyst` against `senior data analyst`, and against the same string with a trailing space,
   both returned `keyed_value_change`.

2. When the stored record carried an `object` and the caller passed none, the branch fell through to
   a text clash that needs a number or a negation word, found nothing, and the call returned `[]`.
   An empty list from a write gate reads as "checked, clean". It had checked nothing. That is the
   house failure shape: a check that cannot fire reporting SAFE.

THE CONTROLS MATTER MORE THAN THE FIXES HERE, because both fixes make the detector quieter, and a
quieter detector is indistinguishable from a broken one unless something still fires:
  * a genuinely different value must still raise `keyed_value_change`;
  * two DIFFERENT CJK values must still differ, since the normaliser deletes punctuation and an
    earlier version of this rule deleted every non-Latin character and collapsed them to equal;
  * with no stored object the negation fallback must survive untouched.
"""
from __future__ import annotations

import inspeximus
from inspeximus import Inspeximus

ROLE = "Rastislav works as a Senior Data Analyst"


def _store(tmp_path, name="s.json"):
    return Inspeximus(path=str(tmp_path / name))


def _kinds(hits):
    return sorted({h["kind"] for h in hits})


def test_a_value_that_differs_only_in_case_or_spacing_is_not_a_contradiction(tmp_path):
    s = _store(tmp_path)
    s.remember(ROLE, key="role", object="Senior Data Analyst")
    for same in ("Senior Data Analyst",          # identical
                 "senior data analyst",          # case only
                 "Senior Data Analyst ",         # trailing space
                 "  Senior   Data Analyst",      # collapsed runs
                 "Senior  Data-Analyst"):        # punctuation
        assert s.check_conflict(ROLE, key="role", object=same) == [], same


def test_but_a_real_value_change_still_fires(tmp_path):
    """The control. Without it the test above is satisfied by a detector that never fires."""
    s = _store(tmp_path)
    s.remember(ROLE, key="role", object="Senior Data Analyst")
    assert _kinds(s.check_conflict(ROLE, key="role", object="Junior Data Analyst")) \
        == ["keyed_value_change"]


def test_two_different_cjk_values_are_still_different(tmp_path):
    """A normaliser that strips non-Latin characters collapses every CJK value onto the empty string
    and reports a flat contradiction as agreement. The rule is Unicode-aware; this proves it."""
    s = _store(tmp_path)
    s.remember("City of record", key="city", object="東京")
    assert _kinds(s.check_conflict("City of record", key="city", object="北京")) \
        == ["keyed_value_change"]
    assert s.check_conflict("City of record", key="city", object="東京") == []


def test_supersession_and_conflict_agree_on_what_the_same_value_means(tmp_path):
    """The defect was two paths answering one question with different rules, so assert they agree."""
    s = _store(tmp_path)
    s.remember(ROLE, key="role", object="Senior Data Analyst")
    rec = [r for r in s.items if r.get("status") == "active"][0]
    assert Inspeximus._obj_sig(rec) == Inspeximus._obj_sig(
        {"object": "  senior   data-analyst  "})
    assert s.check_conflict(ROLE, key="role", object="  senior   data-analyst  ") == []


def test_a_keyed_check_that_could_not_compare_says_so(tmp_path):
    """The stored record has a value, the caller withheld one, so nothing was compared."""
    s = _store(tmp_path)
    s.remember(ROLE, key="role", object="Senior Data Analyst")
    assert _kinds(s.check_conflict(ROLE, key="role")) == ["keyed_value_unchecked"]


def test_unchecked_is_not_returned_when_there_was_nothing_to_compare_against(tmp_path):
    """Control on the control: a record with a key but NO stored value cannot be 'unchecked', and the
    text-clash fallback that serves that case must still work."""
    s = _store(tmp_path)
    s.remember("The service is enabled", key="svc")
    assert _kinds(s.check_conflict("The service is not enabled", key="svc")) \
        == ["keyed_value_change"]
    assert s.check_conflict("The service is enabled", key="svc") == []


def test_supplying_the_value_is_what_turns_unchecked_into_an_answer(tmp_path):
    """The remedy the new kind points at has to work, or the message sends the caller nowhere."""
    s = _store(tmp_path)
    s.remember(ROLE, key="role", object="Senior Data Analyst")
    assert _kinds(s.check_conflict(ROLE, key="role")) == ["keyed_value_unchecked"]
    assert s.check_conflict(ROLE, key="role", object="Senior Data Analyst") == []
    assert _kinds(s.check_conflict(ROLE, key="role", object="Junior Data Analyst")) \
        == ["keyed_value_change"]


def test_the_normaliser_does_not_collapse_unnormalisable_values_onto_each_other(tmp_path):
    """`+++` and `---` both normalise to the empty string. The fallback keeps them distinct."""
    from inspeximus.core import _norm_obj
    assert _norm_obj("+++") != _norm_obj("---")
    assert _norm_obj("+++") == _norm_obj("+++")
