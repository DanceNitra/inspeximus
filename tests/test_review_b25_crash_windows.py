"""AUDIT-A review of B-25: an erasure that stops at any step leaves either the erasure done and proven, or
nothing done. Rows must never leave a segment without a tombstone, and erased text must never outlive a
completed erasure."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_audit_b_erasure_reaches_archive_segments import _archived, _no_env, _store  # noqa: E402,F401
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


class Stop(Exception):
    pass


def _crash_at(monkeypatch, where):
    def boom(*a, **k):
        raise Stop(where)
    if where == "before_tombstones":
        monkeypatch.setattr(Inspeximus, "_flush_tombstones", boom)
    elif where == "before_hot_save":
        monkeypatch.setattr(Inspeximus, "_save", boom)
    elif where == "before_commit":
        monkeypatch.setattr(archive, "commit_erasure", boom)


@pytest.mark.parametrize("where", [
    "before_tombstones", "before_hot_save", "before_commit"])
def test_a_stopped_erasure_never_removes_archived_rows_without_their_tombstones(tmp_path, monkeypatch, where):
    p, ids, hot = _store(tmp_path, monkeypatch)
    target = [ids[1], ids[5]]
    assert set(target) <= _archived(p), "control: the targets are archived"
    with monkeypatch.context() as mp:
        _crash_at(mp, where)
        with pytest.raises(Stop):
            Inspeximus(p).forget(ids=target, request_id="crash")
    # a later, unrelated erasure runs recover() first, as every erasure does
    Inspeximus(p).forget(ids=[ids[2]], request_id="later")
    tombs = {t["memory_id"] for t in Inspeximus(p)._tombstones}
    gone = set(target) - _archived(p)
    unaccounted = sorted(gone - tombs)
    assert not unaccounted, (f"{len(unaccounted)} archived row(s) left their segment with no tombstone "
                             f"after a stop at {where}: {unaccounted}")


@pytest.mark.parametrize("where", ["before_tombstones", "before_hot_save", "before_commit"])
def test_a_stopped_erasure_is_not_certified_while_text_remains(tmp_path, monkeypatch, where):
    p, ids, hot = _store(tmp_path, monkeypatch)
    target = [ids[1], ids[5]]
    with monkeypatch.context() as mp:
        _crash_at(mp, where)
        with pytest.raises(Stop):
            Inspeximus(p).forget(ids=target, request_id="crash")
    cert = Inspeximus(p).erasure_certificate()
    still = set(target) & _archived(p)
    if still:
        assert not cert["self_check"]["verified"], (where, cert["self_check"])
