"""A-45: a store with no read-guard key in the key home looks the key up once per handle, not once per record.

A-30 (3.15.4) made the read guard trust a stored verdict only under this store's key, and asks for the key
once per record. `_guard_key()` cached a key it found and nothing else, so with no key file every record
repeated the lookup: a path hash, the key-location checks and an open() that fails. Measured on 3.15.4 with
an empty key home: 972.5 us per call against 0.19 us with a key; the UserPromptSubmit hook on a
71,772-record store took 65.4 s where a keyed home took 5.93 s. Every existing store is in that state until
its first 3.15.4 write mints the key, and a reader that never writes (the prompt hook) stays in it.

These tests count calls to `_guard_key_file`, which every lookup on disk makes first, so they fail on 3.15.4 on every
OS without depending on the clock.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

N = 200


@pytest.fixture
def homes(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    writer, empty = tmp_path / "writer-home", tmp_path / "empty-home"
    writer.mkdir()
    empty.mkdir()
    store = tmp_path / "store" / "m.json"
    store.parent.mkdir()
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(writer))
    m = Inspeximus(str(store))
    for i in range(N):
        m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic")
    m.flush()
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(empty))
    return str(store), empty


@pytest.fixture
def lookups(monkeypatch):
    """Each lookup on disk starts by computing the key file's path, in every version since A-30."""
    calls = []
    real = core._guard_key_file

    def counted(store_path):
        calls.append(store_path)
        return real(store_path)

    monkeypatch.setattr(core, "_guard_key_file", counted)
    return calls


def test_a_recall_without_a_key_looks_the_key_up_once(homes, lookups):
    store, _ = homes
    m = Inspeximus(store)
    got = m.recall("which make target builds the docs", k=6)
    if not got:
        pytest.fail("control: the recall returned nothing, so the read guard may never have run")
    assert m._guard_key() is None, "control: the empty key home holds a key, so the case did not arise"
    assert len(lookups) <= 1, (
        f"{len(lookups)} key lookups for one recall over {N} records: a missing key is looked up per record")


def test_a_second_recall_on_the_same_handle_looks_up_nothing_more(homes, lookups):
    store, _ = homes
    m = Inspeximus(store)
    m.recall("which make target builds the docs", k=6)
    first = len(lookups)
    m.recall("the build directory", k=6)
    assert len(lookups) == first, "the handle forgot that the key is missing"


def test_a_peer_that_mints_the_key_is_seen_after_the_store_moves(homes, lookups):
    store, _ = homes
    m = Inspeximus(store)
    m.recall("which make target builds the docs", k=6)
    assert m._guard_key() is None
    peer = Inspeximus(store)
    peer.remember("a peer writes under this key home", key="peer", object="p")   # the write mints the key
    peer.flush()
    m.refresh()
    assert m._guard_key() is not None, (
        "the handle kept its cached absence after the store moved, so a key a peer minted is never used")


def test_a_write_mints_the_key_even_after_the_absence_was_cached(homes, lookups):
    store, empty = homes
    m = Inspeximus(store)
    m.recall("which make target builds the docs", k=6)
    assert m._guard_key() is None
    m.remember("this handle writes", key="mine", object="m")
    m.flush()
    assert m._guard_key() is not None, "a write did not mint the key once its absence was cached"
    assert any(os.scandir(empty)), "control: no key file was written into the key home"
