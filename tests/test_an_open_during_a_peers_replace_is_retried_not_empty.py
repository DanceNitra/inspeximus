"""An open that lands on a peer's replace is retried; it neither crashes nor loads an empty store.

WHY. `_durable_replace` retries `os.replace` because Windows refuses to replace a file a reader
holds open. Nothing retried the other side. A file mid-replace is briefly a name whose target cannot
be opened, reported as access denied, and `Inspeximus(path)` raised `PermissionError` on that
instant: 4 of 153,305 opens against 54 replaces on 2.27.7, about one in a hundred under the
twelve-writer harness that first saw it (probes/does_an_open_survive_a_peers_replace.py). The
silent variant is worse: `_stat_sig()` swallowed the same error as ABSENT, so an open that lost the
stat instead of the read loaded an EMPTY store from a file with records in it.

The race is injected rather than waited for: the file call that fails is made to fail a fixed number
of times, on this path only, then behave. Each test fails on 2.27.7; the last is the control that a
file which is genuinely unreadable still raises.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from inspeximus import Inspeximus


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    monkeypatch.setattr(Inspeximus, "_OPEN_ATTEMPTS", 4, raising=False)   # the budget, not the behaviour
    p = tmp_path / "s.json"
    m = Inspeximus(path=str(p))
    m.remember("the staging database is db-7", mtype="fact")
    m.remember("the deploy window is 02:00 UTC", mtype="fact")
    del m
    assert os.path.getsize(p) > 2
    return p


def _fail_first(monkeypatch, cls, name, target, times, exc=PermissionError):
    """Make `cls.name` raise `exc` the first `times` calls on `target`, then defer to the original."""
    real = getattr(cls, name)
    seen = {"n": 0}

    def flaky(self, *a, **kw):
        if os.path.abspath(str(self)) == os.path.abspath(str(target)) and seen["n"] < times:
            seen["n"] += 1
            raise exc(13, "Permission denied", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(cls, name, flaky)
    return seen


def test_a_read_that_hits_a_replace_is_retried_and_loads_the_records(seeded, monkeypatch):
    seen = _fail_first(monkeypatch, pathlib.Path, "read_bytes", seeded, times=2)
    m = Inspeximus(path=str(seeded))
    assert seen["n"] == 2, "the injected failure never fired; the test measured nothing"
    assert sorted(r["text"] for r in m.items) == ["the deploy window is 02:00 UTC",
                                                  "the staging database is db-7"]
    assert m._file_sig == m._stat_sig(), "the signature must describe the file the bytes came from"


def test_a_stat_that_hits_a_replace_does_not_load_an_empty_store(seeded, monkeypatch):
    seen = _fail_first(monkeypatch, pathlib.Path, "stat", seeded, times=1)
    m = Inspeximus(path=str(seeded))
    assert seen["n"] == 1, "the injected failure never fired; the test measured nothing"
    assert len(m.items) == 2, "a transient stat error read as an absent file, and the store loaded empty"
    assert m._file_sig is not Inspeximus._ABSENT


def test_the_retried_open_still_refuses_to_overwrite_a_peer(seeded, monkeypatch):
    """The retry must not weaken the guard: a write that lands after the successful read is still caught."""
    _fail_first(monkeypatch, pathlib.Path, "read_bytes", seeded, times=1)
    m = Inspeximus(path=str(seeded))
    peer = Inspeximus(path=str(seeded))
    peer.remember("the peer landed this", mtype="fact")
    from inspeximus.core import StoreChangedOnDisk
    with pytest.raises(StoreChangedOnDisk):
        m.remember("and this handle must be refused", mtype="fact")


def test_control_a_file_that_stays_unreadable_still_raises(seeded, monkeypatch):
    seen = _fail_first(monkeypatch, pathlib.Path, "read_bytes", seeded, times=10 ** 6)
    with pytest.raises(PermissionError):
        Inspeximus(path=str(seeded))
    assert seen["n"] == Inspeximus._OPEN_ATTEMPTS, "the retry budget was not spent before giving up"


def test_control_the_save_side_stat_still_reads_a_transient_error_as_absent(seeded):
    """`_save` keeps the swallowing form: ABSENT on a transient error produces a refusal, which is safe."""
    m = Inspeximus(path=str(seeded))
    assert m._stat_sig() == m._stat_sig(raise_transient=True)
    m.path = pathlib.Path(str(seeded) + ".missing")
    assert m._stat_sig() is Inspeximus._ABSENT
    assert m._stat_sig(raise_transient=True) is Inspeximus._ABSENT
