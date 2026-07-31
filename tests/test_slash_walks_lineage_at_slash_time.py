"""A retraction must reach what a record DECLARES it depends on, decided when the retraction happens.

`taint` is computed once, inside `remember()`, from whatever the parents' provenance looked like at that
instant. `slash(scope='source')` then picked its targets by intersecting that frozen set with the caught
sources. So a `derived_from` edge that arrives AFTER the write -- an app repairing lineage it discovered
late, which is the ordinary case for a summariser that learns its inputs downstream -- was invisible to
the accountability lever. The descendant declared its dependence on the retracted record and kept full
standing anyway.

`forget_subject` already closed forward over `derived_from` edges. slash() did not. Two operations that
both answer "who does this reach?" walked different graphs, and only one of them was right.

Also added here: scope='lineage'. scope='memory' touches only the named records and scope='source'
forfeits everything sharing a source label; neither serves the common case of catching ONE memory and
wanting the conclusions built on it to lose standing too. That is Doyle's dependency-directed retraction
(AIJ 12(3), 1979) with the justification set given explicitly rather than inferred from a source string.

`test_the_frozen_taint_really_is_empty` is the control: it fails if the fixture stops reproducing the
original defect, so a later refactor cannot leave these passing because the case never arises.
"""
from __future__ import annotations

from inspeximus import Inspeximus

SRC = "runbook2024"


def _store(tmp_path, name="lin.json", **kw):
    return Inspeximus(path=str(tmp_path / name), **kw)


def _repaired_chain(st):
    """A sourced parent, and two descendants whose lineage is declared AFTER they were written.

    This is lineage repair: the summariser did not know its inputs at write time, so the store computed
    an empty taint, and the app attached the edges once it did know.
    """
    parent = st.remember("the runbook says restart the pool at 200 connections", source={"doc": SRC})
    child = st.remember("summary: restart the pool at 200")
    grand = st.remember("policy: pool restarts are automated")
    by = {r["id"]: r for r in st.items}
    by[child]["derived_from"] = [parent]
    by[grand]["derived_from"] = [child]
    return parent, child, grand


def _credit_all(st, *ids):
    for i in ids:
        st.credit(i, True, weight=5.0)


def test_the_frozen_taint_really_is_empty(tmp_path):
    """THE CONTROL. If this stops holding, the fixture no longer builds the situation the fix is for and
    every other test in this file would be passing for the wrong reason."""
    st = _store(tmp_path)
    parent, child, grand = _repaired_chain(st)
    by = {r["id"]: r for r in st.items}
    assert not (by[child].get("taint") or []), (
        "the child now carries a write-time taint, so the old frozen-set path would have reached it "
        "anyway -- this fixture no longer reproduces the defect")
    assert not (by[grand].get("taint") or [])
    assert by[child]["derived_from"] == [parent], "the child must still DECLARE its parent"
    assert SRC in Inspeximus._rec_sources(by[parent]), "the parent must carry the source being slashed"


def test_a_source_slash_reaches_a_descendant_whose_lineage_was_repaired(tmp_path):
    st = _store(tmp_path)
    parent, child, grand = _repaired_chain(st)
    _credit_all(st, parent, child, grand)
    st.slash([parent], scope="source")
    by = {r["id"]: r for r in st.items}
    assert (by[child].get("meta") or {}).get("slashed"), (
        "the child declares derived_from=[parent] and kept its standing through the parent's retraction")
    assert by[child]["good"] == 0.0


def test_it_reaches_transitively_not_just_one_generation(tmp_path):
    st = _store(tmp_path)
    parent, child, grand = _repaired_chain(st)
    _credit_all(st, parent, child, grand)
    st.slash([parent], scope="source")
    by = {r["id"]: r for r in st.items}
    assert (by[grand].get("meta") or {}).get("slashed"), "the depth-2 descendant was not reached"


def test_an_unrelated_record_is_not_touched(tmp_path):
    """The other half: a walk that reaches everything is not a walk, it is a wipe."""
    st = _store(tmp_path)
    parent, child, grand = _repaired_chain(st)
    other = st.remember("unrelated: the cache TTL is 90 seconds", source={"doc": "ops-wiki"})
    st.credit(other, True, weight=5.0)
    st.slash([parent], scope="source")
    by = {r["id"]: r for r in st.items}
    assert not (by[other].get("meta") or {}).get("slashed"), "slash reached a record with no lineage to it"
    assert by[other]["good"] == 5.0


def test_restore_reaches_exactly_as_far_as_slash(tmp_path):
    """An appeal narrower than the penalty leaves records forfeit with no operation able to clear them."""
    st = _store(tmp_path)
    parent, child, grand = _repaired_chain(st)
    _credit_all(st, parent, child, grand)
    st.slash([parent], scope="source")
    st.restore([parent], scope="source")
    by = {r["id"]: r for r in st.items}
    for label, rid in (("child", child), ("grandchild", grand)):
        assert not (by[rid].get("meta") or {}).get("slashed"), "%s stayed forfeit after restore" % label
        assert by[rid]["good"] == 5.0, "%s did not get its exact pre-slash standing back" % label


# ---------------------------------------------------------------- scope='lineage'

def test_lineage_scope_cascades_from_one_caught_memory(tmp_path):
    """The case neither existing scope serves: one memory caught, its conclusions must follow it down."""
    st = _store(tmp_path)
    parent = st.remember("the runbook says restart the pool at 200", source={"doc": SRC})
    sibling = st.remember("the runbook also says drain before restart", source={"doc": SRC})
    child = st.remember("summary: restart at 200", derived_from=[parent])
    _credit_all(st, parent, sibling, child)
    st.slash([parent], scope="lineage")
    by = {r["id"]: r for r in st.items}
    assert (by[child].get("meta") or {}).get("slashed"), "the derived conclusion kept its standing"
    assert not (by[sibling].get("meta") or {}).get("slashed"), (
        "lineage scope forfeited a same-source record it was never asked to touch -- that is what "
        "scope='source' is for")


def test_memory_scope_still_means_only_the_named_records(tmp_path):
    """The contract scope='lineage' exists to avoid breaking."""
    st = _store(tmp_path)
    parent = st.remember("the runbook says restart the pool at 200", source={"doc": SRC})
    child = st.remember("summary: restart at 200", derived_from=[parent])
    _credit_all(st, parent, child)
    st.slash([parent], scope="memory")
    by = {r["id"]: r for r in st.items}
    assert not (by[child].get("meta") or {}).get("slashed"), (
        "scope='memory' documents 'just the named memories' and must keep meaning that")


def test_lineage_restore_mirrors_lineage_slash(tmp_path):
    st = _store(tmp_path)
    parent = st.remember("the runbook says restart the pool at 200", source={"doc": SRC})
    child = st.remember("summary: restart at 200", derived_from=[parent])
    _credit_all(st, parent, child)
    st.slash([parent], scope="lineage")
    st.restore([parent], scope="lineage")
    by = {r["id"]: r for r in st.items}
    assert not (by[child].get("meta") or {}).get("slashed")
    assert by[child]["good"] == 5.0


# ---------------------------------------------------------------- unresolvable parents

def test_a_declared_parent_that_does_not_resolve_is_not_silently_dropped(tmp_path):
    """A typo in derived_from used to yield a record with no lineage AND full primary standing."""
    st = _store(tmp_path)
    rid = st.remember("summary of something", derived_from=["0000deadbeef"])
    rec = {r["id"]: r for r in st.items}[rid]
    assert rec.get("derived_from_unresolved") == ["0000deadbeef"], (
        "the unresolvable parent vanished; nothing records that lineage was claimed and lost")
    assert rec.get("orphan") is True, (
        "lineage was claimed and none of it resolved, yet the record banks primary standing")


def test_a_partially_resolvable_lineage_keeps_the_good_half(tmp_path):
    st = _store(tmp_path)
    parent = st.remember("the runbook says restart the pool at 200", source={"doc": SRC})
    rid = st.remember("summary", derived_from=[parent, "0000deadbeef"])
    rec = {r["id"]: r for r in st.items}[rid]
    assert rec.get("derived_from") == [parent]
    assert rec.get("derived_from_unresolved") == ["0000deadbeef"]
    assert not rec.get("orphan"), "one resolvable parent is lineage; this must not be an orphan"


# ---------------------------------------------------------------- durability and termination

def test_the_walk_survives_a_lost_process(tmp_path):
    """Reach and durability are one guarantee: a retraction that reaches further but is not written down
    is not a retraction that reached further."""
    st = _store(tmp_path)
    parent, child, grand = _repaired_chain(st)
    _credit_all(st, parent, child, grand)
    st.slash([parent], scope="source")
    reopened = Inspeximus(path=str(tmp_path / "lin.json"))
    by = {r["id"]: r for r in reopened.items}
    assert (by[grand].get("meta") or {}).get("slashed"), "the lineage walk did not survive a reopen"
    assert by[grand]["good"] == 0.0


def test_a_cycle_in_the_declared_lineage_terminates(tmp_path):
    """Ids are content-addressed so a real cycle is hard to build, but the walk must not rely on that."""
    st = _store(tmp_path)
    a = st.remember("claim A about the pool", source={"doc": SRC})
    b = st.remember("claim B about the pool", derived_from=[a])
    by = {r["id"]: r for r in st.items}
    by[a]["derived_from"] = [b]          # forge the back-edge directly
    _credit_all(st, a, b)
    st.slash([a], scope="lineage")       # must terminate, not spin
    assert (by[b].get("meta") or {}).get("slashed")
