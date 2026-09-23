# -*- coding: utf-8 -*-
"""Regression test for strict_lineage (opt-in fail-closed gate).

Measured defect (2026-09-21, CREW OS store): remember(derived_from=["<id not in store>"])
returned an id, stored derived_from_unresolved, set orphan=True, left derived_from empty --
and the Python API said nothing. 11 persona layers were written under the belief their
lineage had landed. This test pins BOTH halves: the default still preserves the audit
evidence, and the opt-in refuses to commit.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.core import UnresolvedLineage


def test_default_preserves_evidence_and_commits(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"))
    mid = m.remember("child A", key="k::a", derived_from=["does-not-exist"])
    rec = next(r for r in m.items if r["id"] == mid)
    assert rec.get("derived_from_unresolved") == ["does-not-exist"], "audit evidence must be kept"
    assert rec.get("orphan") is True, "an announced derivation that resolved nothing is an orphan"
    assert not rec.get("derived_from"), "no phantom parent may be recorded as real lineage"


def test_strict_lineage_refuses_to_commit_and_names_the_ids(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), strict_lineage=True)
    before = len(list(m.items))
    try:
        m.remember("child B", key="k::b", derived_from=["bad-one", "bad-two"])
    except UnresolvedLineage as e:
        assert set(e.unresolved) == {"bad-one", "bad-two"}
        assert "bad-one" in str(e) and "bad-two" in str(e), "the message must name every id"
    else:
        raise AssertionError("strict_lineage=True must raise on an unresolvable derived_from id")
    assert len(list(m.items)) == before, "the write must NOT be committed"


def test_strict_lineage_allows_a_resolvable_parent(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), strict_lineage=True)
    parent = m.remember("parent", key="k::p")
    child = m.remember("child", key="k::c", derived_from=[parent])
    rec = next(r for r in m.items if r["id"] == child)
    assert rec.get("derived_from") == [parent], "a resolvable parent must be linked"
    assert not rec.get("derived_from_unresolved")
    assert not rec.get("orphan")


def test_strict_lineage_does_not_affect_writes_without_lineage(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), strict_lineage=True)
    mid = m.remember("plain fact", key="k::plain")
    rec = next(r for r in m.items if r["id"] == mid)
    assert rec.get("status") == "active"
    assert not rec.get("orphan")
