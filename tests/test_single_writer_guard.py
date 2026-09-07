"""The single-writer guard, and the version-mixing hole it cannot close.

The guard shipped in 1.67.0. Within one version it refuses a save over a file that changed underneath the
handle. Across versions it cannot help: a handle from a pre-1.67 release has no guard and saves anyway,
erasing the newer writer's records with no error.

Measured with real installs (1.51.0 alongside 1.69.0 on one store file):

    1.51.0 opens -> 1.69.0 writes and flushes -> 1.51.0 flushes
    final store: ['baseline record', 'written by OLD after the fact']   <- the 1.69.0 record is gone

    same interleave, both on 1.69.0
    final store: ['baseline record', 'written by NEW while OLD held a handle']   <- guard refuses

CI installs one version, so the cross-version half cannot run here; it lives in SECURITY.md with the
measurement. What IS testable is that the guard exists, fires, and that `reload()` is the documented way
out -- if any of that regresses, the warning in SECURITY.md becomes a lie about a guard that is gone.

FROM 3.0.0 THE GUARD HAS TWO ANSWERS, AND THIS FILE TESTS BOTH. A JSON save rewrites the whole file,
so writing over a changed file replaces the other writer's records: refusing is the only safe answer,
and it costs the refused writer its work. A row write touches only the ids it names, so the save
performs the union `reload()` defines and both writers keep their records. The property under test is
the same in both -- nobody's committed write disappears -- so each test runs on both formats and
asserts the refusal only where refusing is what protects it.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _pin(fmt, monkeypatch):
    """Ask for the format under test. A new store is rows by default, so JSON has to be requested."""
    if fmt == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    else:
        monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)


@pytest.mark.parametrize("fmt", ["json", "rows"])
def test_a_second_handle_cannot_clobber_the_first(fmt, monkeypatch):
    _pin(fmt, monkeypatch)
    p = _path()
    a = Inspeximus(path=p)
    a.remember("baseline")
    a.flush()

    # Both handles must be OPEN before either writes, and the guard sits on the SAVE -- `remember()`
    # persists immediately (`_save(force=True)`), so it fires there, not on the later `flush()`.
    first = Inspeximus(path=p)
    second = Inspeximus(path=p)

    first.remember("written by first")
    first.flush()

    if fmt == "json":
        with pytest.raises(StoreChangedOnDisk):
            second.remember("written by second, on a stale view")
    else:
        second.remember("written by second, on a stale view")
        second.flush()
    _texts = [r["text"] for r in Inspeximus(path=p).items]
    if fmt == "rows":
        assert any("written by second" in t for t in _texts), (
            "a row store merges rather than refusing, so the second writer keeps its record too")
    assert any("written by first" in r["text"] for r in Inspeximus(path=p).items), \
        "the first writer's record must survive -- the guard exists to stop exactly this erasure"


@pytest.mark.parametrize("fmt", ["json", "rows"])
def test_reload_is_the_documented_way_out(fmt, monkeypatch):
    """A guard with no recovery path is a wall. `reload()` merges and lets the write proceed."""
    _pin(fmt, monkeypatch)
    p = _path()
    a = Inspeximus(path=p)
    a.remember("baseline")
    a.flush()
    first = Inspeximus(path=p)
    second = Inspeximus(path=p)
    first.remember("written by first")
    first.flush()

    if fmt == "json":
        with pytest.raises(StoreChangedOnDisk):
            second.remember("written by second")
        second.reload()
    second.remember("written by second")
    second.flush()
    texts = [r["text"] for r in Inspeximus(path=p).items]
    assert any("written by first" in t for t in texts), "the other writer's record survives the merge"
    assert any("written by second" in t for t in texts), "and so does ours"


def test_the_guard_does_not_fire_on_a_lone_writer():
    """It must not become an alarm on ordinary single-process use."""
    p = _path()
    m = Inspeximus(path=p)
    for i in range(5):
        m.remember(f"record {i}")
        m.flush()
    assert len(Inspeximus(path=p).items) == 5


def test_the_guard_is_still_importable_under_its_documented_name():
    """SECURITY.md names `StoreChangedOnDisk`. If it is renamed, the warning stops matching the code."""
    from inspeximus import core
    assert issubclass(core.StoreChangedOnDisk, Exception)
