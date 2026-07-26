"""`verify_claim` answered `supported` for a claim its own evidence contradicted.

With no `object` on either side -- the state most real stores are in, since `object=` is optional on
remember() -- the match test was `not (numeric_clash or negation_clash)`. Two different nouns clash
neither way, so:

    store: "the patient is allergic to shellfish"
    claim: "the patient is allergic to peanuts"    ->  verdict: supported
                                                       matched: the shellfish record

"I found nothing that disagrees" was being returned as "the store says so". This is the gate an agent
calls immediately BEFORE asserting something to a person, so the weaker fact reported as the stronger one
is how a fabrication gets licensed -- and it arrived with a citation attached.

The fix does not guess. It separates confirmation from the absence of refutation: `_matches` may now say
"cannot tell", and that becomes its own verdict, `unverifiable`, with the record attached so the caller can
judge. A genuine restatement is still `supported`; a real contradiction is still `contradicted`.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402


def store(*facts, **kw):
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), **kw)
    for f in facts:
        m.remember(f) if isinstance(f, str) else m.remember(**f)
    return m


def test_a_contradicting_record_no_longer_supports_the_claim():
    """THE defect, in the exact shape it shipped."""
    m = store("the patient is allergic to shellfish")
    res = m.verify_claim("the patient is allergic to peanuts")
    assert res["verdict"] != "supported", \
        "a record naming a DIFFERENT allergen must never be returned as support for this claim"
    assert res["verdict"] == "unverifiable", res


@pytest.mark.parametrize("stored,claimed", [
    ("the office is in Berlin", "the office is in Munich"),
    ("the deploy window is Tuesday", "the deploy window is Thursday"),
    ("the primary contact is Anna", "the primary contact is Petra"),
    ("backups go to the Frankfurt region", "backups go to the Dublin region"),
])
def test_the_class_not_just_the_allergen(stored, claimed):
    """One fixture proves one fixture. Every one of these is a categorical swap with no number and no
    negation, which is precisely what the old heuristic could not see."""
    assert store(stored).verify_claim(claimed)["verdict"] != "supported"


def test_a_real_restatement_is_still_supported():
    """The fix must not buy safety by refusing everything -- a verdict that is never `supported` would be
    useless and would look exactly as clean as a correct one."""
    m = store("the patient is allergic to shellfish")
    assert m.verify_claim("the patient is allergic to shellfish")["verdict"] == "supported"
    assert m.verify_claim("allergic to shellfish")["verdict"] == "supported", \
        "a sub-phrase of the stored fact is a restatement, not a new claim"


def test_a_fabrication_is_still_unsupported():
    m = store("the patient is allergic to shellfish")
    assert m.verify_claim("the sprint demo is on Friday at noon")["verdict"] == "unsupported"


def test_the_keyed_paths_are_unchanged():
    """The keyed path has a real value axis and was never the defect; it must stay exact."""
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    m.remember("the retention policy is 90 days", key="p", object="90d")
    assert m.verify_claim("the retention policy is 90 days", key="p", object="90d")["verdict"] == "supported"
    m.remember("the retention policy is 30 days", key="p", object="30d")
    assert m.verify_claim("the retention policy is 90 days", key="p",
                          object="90d")["verdict"] == "stale_superseded"
    assert m.verify_claim("the retention policy is 7 days", key="p", object="7d")["verdict"] == "contradicted"


def test_a_negation_is_still_a_contradiction():
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    m.remember("the office is in Berlin", key="city")
    assert m.verify_claim("the office is not in Berlin", key="city")["verdict"] == "contradicted"


def test_the_undecidable_verdict_carries_the_record_it_could_not_decide_on():
    """`unverifiable` with nothing attached would be indistinguishable from `unsupported` and the caller
    could not act on it."""
    res = store("the patient is allergic to shellfish").verify_claim("the patient is allergic to peanuts")
    assert res["matched"] and res["matched"]["text"], res
    assert "shellfish" in res["matched"]["text"]


def test_only_supported_means_the_store_backs_it():
    """The contract every caller depends on, asserted directly: no verdict other than `supported` may be
    read as grounding, and the docstrings that agents read must say so."""
    import re

    from inspeximus import Inspeximus as I

    # Whitespace-normalised: a docstring wraps, and the first version of this assertion failed only
    # because "NOT grounded" straddled a line break. A test that breaks on reflowing prose gets
    # weakened or deleted the first time someone reformats, which is worse than no test.
    doc = re.sub(r"\s+", " ", I.verify_claim.__doc__)
    assert "'unverifiable'" in doc, "the verdict agents must not read as grounding is undocumented"
    assert "Treat it as NOT grounded" in doc

    mcp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "inspeximus", "mcp_server.py")
    with open(mcp, encoding="utf-8") as fh:
        mcp_src = re.sub(r"\s+", " ", fh.read())
    assert "'unverifiable'" in mcp_src, \
        "agents read the MCP docstring, not this one -- the same fix has to reach both surfaces"
    assert "ONLY 'supported' means the store backs the claim" in mcp_src


def test_the_keyed_path_can_also_be_undecidable():
    """A key does not imply a value. `remember(..., key=...)` without `object=` is ordinary usage, and then
    the keyed path has exactly the same blind spot as the keyless one.

    Getting this wrong in the other direction is not harmless either: without the distinction the record
    falls through to `contradicted`, which asserts that the store DISAGREES with the claim when the truth is
    that it cannot tell. A caller gating on `contradicted` would then correct a user who was right."""
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    m.remember("the patient is allergic to shellfish", key="allergy")      # no object=
    res = m.verify_claim("the patient is allergic to peanuts", key="allergy")
    assert res["verdict"] == "unverifiable", res
    assert res["matched"] and "shellfish" in res["matched"]["text"]

    # and the same key still confirms a genuine restatement
    assert m.verify_claim("the patient is allergic to shellfish",
                          key="allergy")["verdict"] == "supported"


def test_the_keyed_undecidable_case_is_not_reported_as_disagreement():
    """Stated separately because it is the failure mode of the FIX, not of the original defect."""
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    m.remember("deploys are announced in the release channel", key="proc")
    assert m.verify_claim("deploys are announced by the on-call engineer",
                          key="proc")["verdict"] != "contradicted"
