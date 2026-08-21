"""0/0 is not a measurement, and it used to render as 0.0 in two of six coverage fields.

@Stratogain asked @safal207 on safal207/Causal-Memory-Layer#311 whether a `writer_contract_coverage`
of 1.0 can be told apart from a basis that was never enumerated -- a vacuous quantifier over the
empty set -- and cited our own 2.15.0 four-valued split as the precedent for splitting it.

Asked of ourselves, the same defect was here with the sign flipped. `check_sources()` returns six
coverage numbers. Four already returned `None` on an empty denominator, for exactly this reason.
`locator_coverage` and `environment_binding_coverage` returned **0.0**, inside the same dict.

That is worse for us than a vacuous 1.0 would be, because we published a real 0.0 as a finding:
210,499 records, 98.3% carrying a source field, 0.01% resolving to anything re-checkable. Against
that background a vacuous 0.0 does not read as "nothing was measured", it reads as a catastrophe.

The distinction these tests pin:

    empty store        -> None   nothing was measured
    records, no source -> 0.0    measured, and the answer is zero
"""
import os
import tempfile

import pytest

from inspeximus import Inspeximus

COVERAGE = ("locator_coverage", "refetch_verification_coverage",
            "declared_observation_binding_coverage", "observation_binding_coverage",
            "source_enumeration_coverage", "environment_binding_coverage")


def _store(tmp_path):
    return Inspeximus(path=str(tmp_path / "s.json"))


def test_every_coverage_field_is_none_on_an_empty_population(tmp_path):
    cov = _store(tmp_path).check_sources()["coverage"]
    offenders = {k: cov[k] for k in COVERAGE if cov.get(k) is not None}
    assert not offenders, (
        "0/0 is not a measurement; these fields returned a number over an empty population "
        f"and a reader cannot tell that from a measured result: {offenders}")


def test_a_measured_zero_is_still_a_zero(tmp_path):
    """The control that makes the test above mean something. If `None` were returned whenever the
    answer happened to be zero, the first test would pass over a field that had stopped measuring."""
    m = _store(tmp_path)
    m.remember("a record with no source at all")
    cov = m.check_sources()["coverage"]
    assert cov["locator_coverage"] == 0.0
    assert cov["environment_binding_coverage"] == 0.0


def test_the_two_zeros_are_distinguishable(tmp_path):
    """The property in one assertion: same field, same value-space, different meanings."""
    empty = _store(tmp_path).check_sources()["coverage"]["locator_coverage"]
    m = Inspeximus(path=str(tmp_path / "t.json"))
    m.remember("no source here")
    measured = m.check_sources()["coverage"]["locator_coverage"]
    assert empty is None and measured == 0.0 and empty != measured


def test_a_nonzero_coverage_still_reports(tmp_path):
    """And the field is not stuck at None/0.0 -- a record with a re-fetchable source reads 1.0."""
    src = tmp_path / "runbook.md"
    src.write_text("host is db-old", encoding="utf-8")
    m = _store(tmp_path)
    m.remember("host is db-old", source={"doc": str(src)})
    cov = m.check_sources()["coverage"]
    assert cov["locator_coverage"] == 1.0
    assert cov["refetch_verification_coverage"] == 1.0


@pytest.mark.parametrize("field", COVERAGE)
def test_the_field_exists_rather_than_being_omitted(tmp_path, field):
    """An absent key reads as 'not applicable'. None reads as 'not measured'. Keep the key."""
    assert field in _store(tmp_path).check_sources()["coverage"]
