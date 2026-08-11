"""The CML memory-applicability v0.1 contract, evaluated by OUR implementation against THEIR fixture.

safal207 froze a vendor-neutral fixture in Causal-Memory-Layer#270 (discussed on
anthropics/claude-code#34556). A contract with one implementation is a proposal; the useful thing to be
is the second one, and the only way that means anything is to consume the fixture VERBATIM rather than
a paraphrase of it. The file here is a byte copy, not a reconstruction -- a reconstruction tests
whether I understood the contract, which is the question the test is supposed to answer.

Every case asserts a status, and where the fixture declares `expected_reasons` those are asserted too:
agreeing on the verdict while disagreeing on why is not interoperability, it is a coincidence.
"""
import io
import json
import os

import pytest

from inspeximus.core import (APPLICABILITY_PRECEDENCE, APPLICABILITY_RESERVED, APPLICABILITY_STRICT,
                             evaluate_applicability)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                       "cml_memory_applicability_v0.1.json")


def _fixture():
    with io.open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_fixture_is_present_and_is_the_frozen_one():
    """A missing or emptied fixture would make every parametrised test below vanish silently, and an
    empty parametrisation reports green. Assert the shape before trusting the cases."""
    d = _fixture()
    assert d["schema_version"] == "cml-memory-applicability-fixtures-v0.1", d["schema_version"]
    assert len(d["cases"]) >= 15, "the frozen fixture had 15 cases; this has %d" % len(d["cases"])
    assert {c["expected_status"] for c in d["cases"]} == set(APPLICABILITY_PRECEDENCE), (
        "the fixture no longer exercises every verdict: %r"
        % sorted({c["expected_status"] for c in d["cases"]}))


@pytest.mark.parametrize("case", _fixture()["cases"], ids=lambda c: c["id"])
def test_every_frozen_case_agrees(case):
    got = evaluate_applicability(case.get("source"), case.get("stored_environment"),
                                 case.get("current_environment"), case.get("caller_metadata"),
                                 now=_fixture()["now"])
    assert got["status"] == case["expected_status"], (
        "%s: contract expects %s, we returned %s (%r)"
        % (case["id"], case["expected_status"], got["status"], got["reasons"]))
    for reason in case.get("expected_reasons") or []:
        assert reason in got["reasons"], (
            "%s: contract expects reason %r; we gave %r -- agreeing on the verdict while disagreeing on "
            "why is not interoperability" % (case["id"], reason, got["reasons"]))


def test_source_failures_outrank_environment_drift():
    """The precedence is the load-bearing part: a record whose ORIGIN cannot be verified is broken for
    everyone, while one that is merely inapplicable here may be fine elsewhere. If a weaker REVALIDATE
    could mask DRIFT, a changed source would be reported as a context problem and re-run somewhere else."""
    drifted = {"locator": "x", "refetchable": True, "exists": True,
               "expected_digest": "a", "observed_digest": "b"}
    got = evaluate_applicability(drifted, {"tenant": "a"}, {"tenant": "b"})
    assert got["status"] == "DRIFT", got


def test_absence_of_historical_binding_is_not_permission():
    """The sharpest rule in the contract, and one our source-only check has no name for: evidence that
    never bound a repository or commit must not acquire authority in a new code context on the strength
    of a source digest that never moved."""
    clean = {"locator": "x", "refetchable": True, "exists": True,
             "expected_digest": "a", "observed_digest": "a"}
    got = evaluate_applicability(clean, {}, {"repository": "org/repo", "commit_sha": "abc"})
    assert got["status"] == "REVALIDATE", got
    assert "environment_unbound:repository" in got["reasons"], got
    assert "environment_unbound:commit_sha" in got["reasons"], got

    # CONTROL: a non-strict dimension unbound is NOT enough on its own, or every record would revalidate
    # forever and the verdict would carry no information.
    assert evaluate_applicability(clean, {}, {"tenant": "t"})["status"] == "MATCH"


def test_a_caller_cannot_manufacture_trust_state():
    for key in sorted(APPLICABILITY_RESERVED) + ["_cml_internal"]:
        got = evaluate_applicability({"locator": "x", "refetchable": True, "exists": True,
                                      "expected_digest": "a", "observed_digest": "a"},
                                     {}, {}, {key: "earned"})
        assert got["status"] == "REJECT", "%s was accepted: %r" % (key, got)


def test_an_unparseable_expiry_never_grants_authority_silently():
    """A mis-parsed timestamp must not become 'not expired'. Garbage in valid_until leaves the verdict
    unchanged rather than quietly clearing an expiry that may have passed."""
    clean = {"locator": "x", "refetchable": True, "exists": True,
             "expected_digest": "a", "observed_digest": "a"}
    got = evaluate_applicability(clean, {"valid_until": "not-a-timestamp"}, {},
                                 now="2026-08-11T12:00:00Z")
    assert got["status"] == "MATCH", got          # documented behaviour, asserted so a change is visible
    real = evaluate_applicability(clean, {"valid_until": "2026-08-11T11:00:00Z"}, {},
                                  now="2026-08-11T12:00:00Z")
    assert real["status"] == "REVALIDATE" and "binding_expired" in real["reasons"], real


def test_strict_dimensions_are_a_declared_list_not_an_inference():
    assert APPLICABILITY_STRICT == ("repository", "commit_sha")
