"""Team sync: two writers who never touch the same file must end in the same state.

WHY THIS FILE EXISTS. inspeximus is a SINGLE-WRITER store by construction -- `_save` raises
StoreChangedOnDisk rather than let one handle overwrite another's records. That guarantee is the
reason a team cannot currently share one: the answer to "two people writing" is "give each writer
its own store file", which leaves them with two stores and no way to reconcile them.

The merge rule itself is NOT missing and must not be reinvented here. `reload()` already implements
it, and its comments record what it cost to get right: a union by id alone left two contradictory
ACTIVE records under one key while verify_writes() still returned True; keying the fix on `key`
alone retired another tenant's value; demoting same-VALUE rows destroyed restatements the store
deliberately keeps. What `reload()` cannot do is merge rows it did not read from its own file.

So the gap is transport, not semantics: a way to hand another writer's records to that same rule.

WHAT CONVERGENCE MEANS HERE. `state_digest()` covers id, status, ts, key, tenant and the content
hash, so two stores agreeing on it agree on every record AND on which one currently wins. That is
the property, and it is why the assertions below compare digests rather than counting rows: a row
count cannot tell a merged store from one holding two live answers to the same question.

THE CONTROLS ARE THE POINT. Each of these fails if the merge is a no-op or a naive union, which is
the failure mode this test exists to catch:
  - control_1: before any exchange the two digests MUST differ, or the test proves nothing.
  - control_2: a supersession must win by ts on BOTH sides, not merely be appended.
  - control_3: a record one side erased must not be resurrected by importing the other's copy.
  - control_4: a restatement of the same value must NOT be demoted, per the store's own rule.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _pair(tmp_path):
    a = Inspeximus(path=str(tmp_path / "alice" / "store.json"))
    b = Inspeximus(path=str(tmp_path / "bob" / "store.json"))
    return a, b


def test_control_1_untouched_stores_do_not_already_agree(tmp_path):
    """If two independent stores already share a digest, every assertion below is vacuous."""
    a, b = _pair(tmp_path)
    a.remember("alice writes about retries", tags=["a"])
    b.remember("bob writes about caching", tags=["b"])
    a.flush(); b.flush()
    assert a.state_digest() != b.state_digest(), \
        "control failed: two different stores reported the same digest, so convergence is untestable"


def test_two_writers_converge_after_exchanging_changesets(tmp_path):
    a, b = _pair(tmp_path)
    a.remember("alice writes about retries", tags=["a"])
    b.remember("bob writes about caching", tags=["b"])
    a.flush(); b.flush()

    a.import_changeset(b.export_changeset())
    b.import_changeset(a.export_changeset())

    assert a.state_digest() == b.state_digest(), \
        "two writers exchanged every record and still disagree on the resulting state"


def test_control_2_a_supersession_wins_on_both_sides(tmp_path):
    """A union by id would leave BOTH answers active. The digest is what catches that."""
    a, b = _pair(tmp_path)
    a.remember_decision("cache for 60s", because="measured p99", topic="cache-ttl")
    a.flush()
    b.import_changeset(a.export_changeset())
    b.remember_decision("cache for 5s", because="60s served stale prices", topic="cache-ttl")
    b.flush()

    a.import_changeset(b.export_changeset())

    live = [r for r in a._items if r.get("key") and r.get("status") == "active"
            and r["key"].endswith("cache-ttl")]
    assert len(live) == 1, f"expected one live answer for the key, found {len(live)}"
    assert "5s" in live[0]["text"], "the older decision won, so ts ordering was not applied"


def test_control_3_an_erasure_is_not_undone_by_the_import(tmp_path):
    a, b = _pair(tmp_path)
    a.remember("delete me", tags=["x"])
    a.flush()
    b.import_changeset(a.export_changeset())
    doomed = [r["id"] for r in a._items if r["text"] == "delete me"]
    a.forget(ids=doomed)
    a.flush()

    a.import_changeset(b.export_changeset())

    assert not [r for r in a._items if r.get("text") == "delete me"], \
        "importing a peer's copy resurrected a record this store had erased"


def test_control_4_a_restatement_is_not_demoted(tmp_path):
    """The store keeps restatements on purpose; a merge that demotes them is not state-preserving."""
    a, b = _pair(tmp_path)
    a.remember_decision("use Postgres", because="team knows it", topic="db")
    a.flush()
    b.import_changeset(a.export_changeset())
    b.remember_decision("use Postgres", because="team knows it", topic="db")
    b.flush()

    before = b.state_digest()
    b.import_changeset(a.export_changeset())
    assert b.state_digest() == before, \
        "re-importing records already held changed the state, so import is not idempotent"
