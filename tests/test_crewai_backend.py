# -*- coding: utf-8 -*-
"""`InspeximusMemoryBackend` against CrewAI's 1.x StorageBackend protocol.

Satisfying the protocol is a type check and proves almost nothing: a class can carry all fourteen
members and do the wrong thing in every one of them. So the protocol check is one assertion here and
the rest is behaviour -- ranking, filtering, scope arithmetic, and an erasure verified on disk rather
than through the API that performed it.

THREE OF THESE EXPECTATIONS WERE WRONG THE FIRST TIME, and the adapter was right:

  * `list_records(offset=1)` returns records in created_at order, not insertion order, so a record
    backdated ten days sorts first and the window moves.
  * `search(min_score=0.0)`, the protocol's default, INCLUDES an orthogonal match, because a cosine
    of exactly 0.0 meets a minimum of 0.0.
  * cos([1,0],[0.9,0.1]) is 0.9939, so it survives `min_score=0.99`.

CrewAI's own conformance file makes the same point about itself: "a hand-written expectation turns a
correct adapter red; the reference cannot." Each number below is computed rather than assumed.
"""
import math
from datetime import datetime, timedelta, timezone

import pytest

# The MODULE, not the distribution. `StorageBackend` arrived in the 1.x line, and CI resolves every
# extra together, which backtracks crewai to a release that predates it -- so importing this at module
# scope would error the whole file at COLLECTION on exactly the environment CI runs.
pytest.importorskip("crewai.memory.storage.backend")

from crewai.memory.storage.backend import MemoryRecord, StorageBackend  # noqa: E402

from inspeximus import Inspeximus                                       # noqa: E402
from inspeximus.integrations.crewai import InspeximusMemoryBackend      # noqa: E402

NOW = datetime.now(timezone.utc)


def _rec(i, scope="/", cats=None, vec=None, md=None, when=None):
    return MemoryRecord(id="r%d" % i, content="fact %d" % i, scope=scope,
                        categories=list(cats or []), metadata=dict(md or {}), importance=0.5,
                        created_at=when or NOW, last_accessed=NOW, embedding=vec,
                        source="test", private=False)


@pytest.fixture
def backend(tmp_path):
    return InspeximusMemoryBackend(path=str(tmp_path / "s.json"))


@pytest.fixture
def filled(backend):
    backend.save([_rec(1, "/team/a", ["notes"], [1.0, 0.0], {"u": "x"}),
                  _rec(2, "/team/b", ["notes", "logs"], [0.0, 1.0], {"u": "y"}),
                  _rec(3, "/team/a/deep", ["logs"], [0.9, 0.1], {"u": "x"}),
                  _rec(4, "/other", ["notes"], None, {"u": "z"}, NOW - timedelta(days=10))])
    return backend


def test_it_satisfies_the_protocol(backend):
    missing = sorted(a for a in getattr(StorageBackend, "__protocol_attrs__", [])
                     if not hasattr(backend, a))
    assert isinstance(backend, StorageBackend), "missing: %s" % ", ".join(missing)


def test_counts_and_lookup(filled):
    assert filled.count() == 4
    assert filled.count("/team") == 3
    assert filled.get_record("r2").content == "fact 2"
    assert filled.get_record("no-such-id") is None


def test_scope_arithmetic(filled):
    assert filled.list_scopes("/") == ["/other", "/team"]
    assert filled.list_scopes("/team") == ["/team/a", "/team/b"]
    info = filled.get_scope_info("/team")
    assert info.record_count == 3
    assert sorted(info.categories) == ["logs", "notes"]
    assert info.child_scopes == ["/team/a", "/team/b"]
    empty = filled.get_scope_info("/nothing")
    assert empty.record_count == 0 and empty.categories == [] and empty.child_scopes == []


def test_list_categories_counts_per_scope(filled):
    assert filled.list_categories("/team") == {"notes": 2, "logs": 2}


def test_search_ranks_by_cosine_and_honours_every_filter(filled):
    ranked = [r.id for r, _ in filled.search([1.0, 0.0], limit=5)]
    assert ranked[:2] == ["r1", "r3"], ranked
    assert "r4" not in ranked, "a record with no embedding cannot be scored and must be skipped"
    assert [r.id for r, _ in filled.search([1.0, 0.0], scope_prefix="/team/a/deep")] == ["r3"]
    assert [r.id for r, _ in filled.search([0.0, 1.0], categories=["logs"])] == ["r2", "r3"]
    assert [r.id for r, _ in filled.search([1.0, 0.0], metadata_filter={"u": "x"})] == ["r1", "r3"]


def test_min_score_is_a_minimum_not_a_strict_bound(filled):
    """0.0 is the protocol default, and an orthogonal match scores exactly 0.0, so it is a hit."""
    assert "r2" in [r.id for r, _ in filled.search([1.0, 0.0])]
    exact = math.sqrt(0.82)                       # |[0.9, 0.1]|
    assert 0.9 / exact > 0.99                     # r3 clears 0.99 on the arithmetic, not on faith
    assert [r.id for r, _ in filled.search([1.0, 0.0], min_score=0.99)] == ["r1", "r3"]
    assert [r.id for r, _ in filled.search([1.0, 0.0], min_score=0.999)] == ["r1"]


def test_update_replaces_without_duplicating(filled):
    filled.update(_rec(1, "/team/a", ["notes"], [1.0, 0.0], {"u": "x"}).model_copy(
        update={"content": "fact 1 CORRECTED"}))
    assert filled.get_record("r1").content == "fact 1 CORRECTED"
    assert filled.count() == 4


def test_delete_filters_and_really_erases(filled, tmp_path):
    assert filled.delete(older_than=NOW - timedelta(days=1)) == 1        # only the backdated one
    assert filled.delete(record_ids=["r2"]) == 1
    assert filled.count() == 2

    # THE POINT. Checked on disk, not through the object that performed the deletion: an earlier
    # adapter in this file set a status flag in memory and never wrote, so a reload brought every
    # "deleted" record back with its content intact.
    filled.store.flush()
    fresh = Inspeximus(path=str(tmp_path / "s.json"))
    assert not any("fact 2" in (r.get("text") or "") for r in fresh.items)


def test_reset_is_scoped(filled):
    """A scoped reset clears its scope and leaves every other one standing.

    The first version of this asserted the store was empty afterwards, which would have passed for a
    reset that ignored its argument and wiped everything -- the exact defect the assertion is for.
    """
    filled.reset("/team")
    assert filled.count("/team") == 0
    assert filled.count("/other") == 1, "a scoped reset removed records outside its scope"
    assert filled.count() == 1


@pytest.mark.asyncio
async def test_async_variants_do_the_same_thing(tmp_path):
    b = InspeximusMemoryBackend(path=str(tmp_path / "a.json"))
    await b.asave([_rec(9, "/x", ["c"], [1.0, 0.0])])
    assert [r.id for r, _ in await b.asearch([1.0, 0.0])] == ["r9"]
    assert await b.adelete(record_ids=["r9"]) == 1
    assert b.count() == 0
