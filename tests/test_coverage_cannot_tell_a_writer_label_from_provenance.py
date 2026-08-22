"""Two stores, both at locator_coverage 1.0, one of which can trace nothing.

Measured on our own eight agent stores on 2026-08-22: 217,549 records, `source` populated on
**100.0%** of them, and **eight distinct values in total** — one per store, each the store's own name.
`agent:scholar` appears in all 26,928 records of one of them. Ratio 0.000037. Re-checkable: zero.

Nothing was broken. The schema said `source: str`, every record had one, and the check that read it
counted the non-empty ones. `locator_coverage` was answering "is the field populated", and it was
being read as "is anything traceable".

`distinct_source_ratio` separates them for the cost of one pass. Near 1/N the field is a writer label;
near 1.0 it is provenance; in between it says which fraction of the store is which.

These tests fail if the two stores below ever become indistinguishable again.
"""
import pytest

from inspeximus import Inspeximus


def _store(tmp_path, name):
    return Inspeximus(path=str(tmp_path / name))


def _writer_label(tmp_path, n=8):
    """Every record stamped with the identity of the process that wrote it. Our own shape."""
    m = _store(tmp_path, "writer.json")
    for i in range(n):
        m.remember("observation %d" % i, source={"doc": "agent:scholar"})
    return m


def _real_provenance(tmp_path, n=8):
    """Every record pointing at a distinct origin."""
    m = _store(tmp_path, "prov.json")
    for i in range(n):
        m.remember("observation %d" % i, source={"doc": "runbook-%02d.md" % i})
    return m


def test_locator_coverage_cannot_tell_them_apart(tmp_path):
    """The control that gives the rest of the file its point: the OLD number is identical."""
    a = _writer_label(tmp_path).check_sources()["coverage"]
    b = _real_provenance(tmp_path).check_sources()["coverage"]
    assert a["locator_coverage"] == 1.0
    assert b["locator_coverage"] == 1.0
    assert a["locator_coverage"] == b["locator_coverage"], (
        "if these ever differ, this test is no longer demonstrating the gap it exists for")


def test_the_ratio_does_tell_them_apart(tmp_path):
    a = _writer_label(tmp_path, 8).check_sources()["coverage"]
    b = _real_provenance(tmp_path, 8).check_sources()["coverage"]
    assert a["distinct_sources"] == 1
    assert a["distinct_source_ratio"] == pytest.approx(1 / 8)
    assert b["distinct_sources"] == 8
    assert b["distinct_source_ratio"] == 1.0
    assert a["distinct_source_ratio"] < b["distinct_source_ratio"]


def test_the_ratio_falls_as_the_store_grows_under_one_label(tmp_path):
    """1/N, not a constant: the bigger the store the worse a single label looks, which is correct.

    This is the shape of the real finding -- 8 values over 217,549 records reads 0.000037 precisely
    because the denominator grew for months while the numerator did not.
    """
    small = _writer_label(tmp_path, 4).check_sources()["coverage"]["distinct_source_ratio"]
    big = Inspeximus(path=str(tmp_path / "big.json"))
    for i in range(40):
        big.remember("observation %d" % i, source={"doc": "agent:scholar"})
    assert big.check_sources()["coverage"]["distinct_source_ratio"] < small


def test_empty_population_is_none_not_a_number(tmp_path):
    """Same contract as the six coverage fields: 0/0 is not a measurement (2.19.1)."""
    cov = _store(tmp_path, "e.json").check_sources()["coverage"]
    assert cov["distinct_source_ratio"] is None
    assert cov["distinct_sources"] == 0
    assert "distinct_source_ratio" in cov, "absent reads as not-applicable; keep the key"


def test_records_without_a_source_are_not_in_the_denominator(tmp_path):
    """The denominator is records that HAVE a source, not all records -- otherwise a store with one
    good source and a thousand blanks would score 0.001 and look like a writer label."""
    m = _store(tmp_path, "mixed.json")
    m.remember("has one", source={"doc": "runbook.md"})
    for i in range(9):
        m.remember("has none %d" % i)
    cov = m.check_sources()["coverage"]
    assert cov["distinct_sources"] == 1
    assert cov["distinct_source_ratio"] == 1.0, (
        "one source, one distinct value: the field is perfectly distinct over what it covers, and "
        "locator_coverage is the number that reports the 10%")
    assert cov["locator_coverage"] == pytest.approx(0.1)
