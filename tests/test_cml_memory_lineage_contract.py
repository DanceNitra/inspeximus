"""CML memory-lineage v0.1, evaluated by our implementation against THEIR frozen fixture.

The second contract in this interoperability loop (Causal-Memory-Layer#272, discussed on
anthropics/claude-code#34556). It came out of a case we raised: a consolidation summary derived from
records about one subject, where one source record is later erased under a deletion request. The
summary stays historically accurate, its own source never changed, and it is MATCH on every
source-integrity and environment dimension -- while still carrying content the subject had removed. The
check that catches it is neither source nor environment; it is the store's own lineage.

The fixture is copied BYTE FOR BYTE, as with v0.1 of the applicability contract. A reconstruction would
test whether I understood the contract, which is the question the test exists to answer.

THIS FIXTURE CARRIES A BENCHMARK CONTRACT OF ITS OWN, and it is the part worth copying into other
people's harnesses: every invalidation case ships a PAIRED NEGATIVE CONTROL that must return MATCH, and
the excluded case must be counted rather than dropped. An exclusion nobody counts is how a clean number
gets manufactured; a case with no negative control cannot distinguish "the checker fired correctly" from
"the checker fires at everything".
"""
import io
import json
import os

import pytest

from inspeximus.core import APPLICABILITY_DEAD_STATES, evaluate_applicability

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                       "cml_memory_lineage_v0.1.json")


def _fx():
    with io.open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _included(d):
    return [c for c in d["cases"] if not c.get("exclude")]


def _evaluate(d, lineage):
    return evaluate_applicability(d["source"], d.get("stored_environment"),
                                  d.get("current_environment"), {}, now=d.get("now"),
                                  lineage=lineage)


def test_the_fixture_is_the_frozen_one_and_its_contract_is_honoured():
    d = _fx()
    assert d["schema_version"] == "cml-memory-lineage-fixtures-v0.1", d["schema_version"]
    contract = d["benchmark_contract"]
    included, excluded = _included(d), [c for c in d["cases"] if c.get("exclude")]
    assert len(included) == contract["expected_included"], (
        "contract declares %d included cases, fixture holds %d"
        % (contract["expected_included"], len(included)))
    assert len(excluded) == contract["expected_excluded"], (
        "contract declares %d excluded, fixture holds %d" % (contract["expected_excluded"], len(excluded)))
    if contract.get("negative_control_required"):
        missing = [c["id"] for c in included if "negative_control" not in c]
        assert not missing, "the contract requires a paired negative control; missing on %r" % missing
    if contract.get("exclusion_accounting_required"):
        unexplained = [c["id"] for c in excluded if not c.get("exclusion_reason")]
        assert not unexplained, "excluded without a stated reason: %r" % unexplained


@pytest.mark.parametrize("case", _included(_fx()), ids=lambda c: c["id"])
def test_every_included_case_agrees(case):
    got = _evaluate(_fx(), case["lineage"])
    assert got["status"] == case["expected_status"], (
        "%s: contract expects %s, we returned %s (%r)"
        % (case["id"], case["expected_status"], got["status"], got["reasons"]))
    for reason in case.get("expected_reasons") or []:
        assert reason in got["reasons"], (
            "%s: contract expects reason %r, we gave %r -- agreeing on the verdict while disagreeing on "
            "why is a coincidence, not interoperability" % (case["id"], reason, got["reasons"]))


@pytest.mark.parametrize("case", _included(_fx()), ids=lambda c: c["id"] + "-negative-control")
def test_every_paired_negative_control_returns_match(case):
    """Without these, a checker that returns REVALIDATE for everything scores a perfect run."""
    nc = case["negative_control"]
    got = _evaluate(_fx(), nc["lineage"])
    assert got["status"] == nc["expected_status"], (
        "%s negative control: expected %s, got %s (%r) -- the checker fires when it should not"
        % (case["id"], nc["expected_status"], got["status"], got["reasons"]))


def test_a_dead_upstream_state_beats_a_missing_digest():
    """State is read BEFORE digest, and the fixture makes the difference visible: an `erased` dependency
    also has no observed digest, so a digest-first reading would report it as merely unverifiable and
    lose the fact that the parent was deliberately removed. Different words, different remedy."""
    dep = {"dependency_id": "dep-a", "state": "erased", "expected_digest": "a", "observed_digest": None}
    got = evaluate_applicability({"locator": "x", "refetchable": True, "exists": True,
                                  "expected_digest": "a", "observed_digest": "a"}, {}, {}, {},
                                 lineage=[dep])
    assert "lineage_invalidated:dep-a:erased" in got["reasons"], got
    assert not any(r.startswith("lineage_unverifiable") for r in got["reasons"]), got


def test_source_integrity_still_outranks_lineage():
    """Precedence is unchanged by the new dimension: a record whose own source drifted is broken for
    everyone, and must not be reported as a lineage problem to be re-derived somewhere else."""
    got = evaluate_applicability({"locator": "x", "refetchable": True, "exists": True,
                                  "expected_digest": "a", "observed_digest": "b"}, {}, {}, {},
                                 lineage=[{"dependency_id": "d", "state": "erased"}])
    assert got["status"] == "DRIFT", got


def test_omitting_lineage_changes_nothing():
    """The dimension is additive. Every caller who never passes lineage must see exactly what they saw
    before it existed, or this is a breaking change wearing a feature's clothes."""
    clean = {"locator": "x", "refetchable": True, "exists": True,
             "expected_digest": "a", "observed_digest": "a"}
    assert evaluate_applicability(clean, {}, {}, {})["status"] == "MATCH"
    assert evaluate_applicability(clean, {}, {}, {}, lineage=[])["status"] == "MATCH"


def test_the_dead_state_list_is_declared_not_inferred():
    assert "erased" in APPLICABILITY_DEAD_STATES and "superseded" in APPLICABILITY_DEAD_STATES
