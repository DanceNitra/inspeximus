"""A-37: a peer's write that moves neither the mtime nor the size is refused over, never overwritten.

The single-writer guard in `_save` compared (st_mtime_ns, st_size) alone. A peer's write in the same clock
tick (1 to 16 ms on Windows) that leaves the file size unchanged moves neither, so on a JSON or encrypted
store, whose save rewrites the whole file, a stale handle overwrote it and the peer's write was gone. The
tests pin the clock with os.utime, so they fail on every OS and do not depend on the host's timer.
"""
import os
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import new_encryption_key  # noqa: E402
from inspeximus.core import Inspeximus, StoreChangedOnDisk  # noqa: E402


@pytest.fixture(autouse=True)
def _json_store(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")


KEY = new_encryption_key()


def _open(p, encrypted):
    return Inspeximus(p, encrypt_key=KEY) if encrypted else Inspeximus(p)


def _seeded(tmp_path, encrypted):
    p = str(tmp_path / "s.json")
    m = _open(p, encrypted)
    x = m.remember("the deploy target is staging", key="deploy", object="staging")
    m.credit([x], outcome=1.0)                       # good=1.0, so the peer's next credit keeps the length
    m.flush()
    return p, x


def _same_tick(p, st):
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))


@pytest.mark.parametrize("encrypted", [False, True], ids=["json", "encrypted"])
def test_a_stale_handle_does_not_overwrite_a_same_tick_same_size_write(tmp_path, encrypted):
    p, x = _seeded(tmp_path, encrypted)
    stale, peer = _open(p, encrypted), _open(p, encrypted)
    st = os.stat(p)
    peer.credit([x], outcome=1.0)                    # good 1.0 -> 2.0: same length
    peer.flush()
    _same_tick(p, st)
    if (os.stat(p).st_mtime_ns, os.stat(p).st_size) != (st.st_mtime_ns, st.st_size):
        pytest.fail("control: the peer's write changed the stat signature, so the case did not arise")
    with pytest.raises(StoreChangedOnDisk):
        # UNKEYED: the save guard is under test. A keyed write decides under the lock and merges first
        # (A-42/A-43, 3.15.6), which keeps the peer's write by merging rather than refusing.
        stale.remember("the stale handle writes")
        stale.flush()
    good = next(r for r in _open(p, encrypted)._items if r["id"] == x).get("good")
    assert good == 2.0, f"the peer's write was overwritten (good={good})"


@pytest.mark.parametrize("encrypted", [False, True], ids=["json", "encrypted"])
def test_a_keyed_write_after_a_same_tick_same_size_write_merges_and_keeps_it(tmp_path, encrypted):
    """The keyed path of the case above (AUDIT-A's review of 3.15.6). A keyed write decides from the rows,
    so it merges what the peer committed in the same tick and lands; it neither refuses nor overwrites.
    On 3.15.3 this stale write overwrote the peer's credit (good stayed 1.0)."""
    p, x = _seeded(tmp_path, encrypted)
    stale, peer = _open(p, encrypted), _open(p, encrypted)
    st = os.stat(p)
    peer.credit([x], outcome=1.0)                    # good 1.0 -> 2.0: same length
    peer.flush()
    _same_tick(p, st)
    if (os.stat(p).st_mtime_ns, os.stat(p).st_size) != (st.st_mtime_ns, st.st_size):
        pytest.fail("control: the peer's write changed the stat signature, so the case did not arise")
    stale.remember("the stale handle's note", key="note", object="n")
    stale.flush()
    rows = _open(p, encrypted)._items
    good = next(r for r in rows if r["id"] == x).get("good")
    assert good == 2.0, f"the peer's same-tick write was lost (good={good})"
    assert any(r.get("key") == "note" and r.get("status", "active") == "active" for r in rows), (
        "the stale handle's keyed write did not land")


@pytest.mark.parametrize("encrypted", [False, True], ids=["json", "encrypted"])
def test_a_handles_own_consecutive_saves_are_not_refused(tmp_path, encrypted):
    """The hash is re-synced from the bytes written; a stale hash would refuse the handle's own next save.

    UNKEYED WRITES ONLY (3.15.6, AUDIT-A's review): credit() and a keyed remember() decide from the rows,
    so they merge under the lock first, and a merge absorbs a stale hash instead of refusing. Then this
    test could not see the mutant that keeps the previous read's hash. An unkeyed write reaches the save
    guard alone."""
    p, x = _seeded(tmp_path, encrypted)
    m = _open(p, encrypted)
    before = len(_open(p, encrypted)._items)
    for i in range(4):
        m.remember(f"own write {i}")
        m.flush()
    assert len(_open(p, encrypted)._items) == before + 4


def test_after_its_own_save_a_handle_still_sees_a_same_tick_peer(tmp_path):
    """The hash must follow THIS handle's write, or a peer's write right after it would read as ours."""
    p, x = _seeded(tmp_path, False)
    a, b = Inspeximus(p), Inspeximus(p)
    a.remember("a writes first", key="a", object="a")
    a.flush()
    b.refresh()
    st = os.stat(p)
    b.credit([x], outcome=1.0)
    b.flush()
    _same_tick(p, st)
    if os.stat(p).st_size != st.st_size:
        pytest.fail("control: the peer changed the size")
    with pytest.raises(StoreChangedOnDisk):
        # UNKEYED: the save guard is under test. A keyed write decides under the lock and merges first
        # (A-42/A-43, 3.15.6), which keeps the peer's write by merging rather than refusing.
        a.remember("a writes again")
        a.flush()


_PEER = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, {root!r})
    os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    from inspeximus.core import Inspeximus
    m = Inspeximus({path!r})
    m.remember("the peer writes inside the window", key="peer", object="p")
    m.flush()
""")


def test_a_locking_peer_cannot_write_between_the_check_and_the_replace(tmp_path, monkeypatch):
    """The comparison and the replace share one hold of the store lock. A peer started inside that window
    must wait for it and land after it, so neither write is lost."""
    import inspeximus.core as core
    p, x = _seeded(tmp_path, False)
    m = Inspeximus(p)
    real = m._disk_hash
    procs = []

    def hash_then_start_a_peer():
        h = real()
        procs.append(subprocess.Popen([sys.executable, "-c", _PEER.format(root=ROOT, path=p)], stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        time.sleep(1.0)                               # the peer is blocked on the lock by now
        return h

    monkeypatch.setattr(m, "_disk_hash", hash_then_start_a_peer)
    m.remember("the handle writes", key="mine", object="m")
    m.flush()
    monkeypatch.undo()
    assert procs, "control: the guard never compared content, so the window was not exercised"
    out = procs[0].communicate(timeout=120)
    texts = {r.get("text") for r in Inspeximus(p)._items}
    assert "the handle writes" in texts, "the peer's write inside the window replaced the handle's"
    # The peer loaded the file before the handle's write, so it must be refused loudly, or have merged;
    # what it may never do is vanish without an error.
    if "the peer writes inside the window" not in texts:
        assert procs[0].returncode != 0, "the peer reported success and its write is not on disk"


def test_an_unlocked_write_right_after_the_replace_is_still_seen(tmp_path, monkeypatch):
    """The hash is taken from the bytes this handle wrote, never from a re-read: a writer that holds no lock
    and changes the file just after the replace must still read as a change at the next save."""
    import inspeximus.core as core
    p, x = _seeded(tmp_path, False)
    m = Inspeximus(p)
    real = core._durable_replace
    hit = []

    def replace_then_an_unlocked_writer(path, payload, *a, **k):
        real(path, payload, *a, **k)
        if str(path) == p and not hit:
            st = os.stat(p)
            data = open(p, "rb").read()
            assert b"the deploy target is staging" in data
            open(p, "wb").write(data.replace(b"the deploy target is staging", b"the deploy target is STAGING"))
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
            hit.append(1)

    monkeypatch.setattr(core, "_durable_replace", replace_then_an_unlocked_writer)
    m.remember("the handle writes", key="mine", object="m")
    m.flush()
    monkeypatch.undo()
    if not hit:
        pytest.fail("control: the unlocked writer never ran")
    with pytest.raises(StoreChangedOnDisk):
        # UNKEYED: this test is about the save guard. A keyed write decides under the lock and merges
        # first (A-42/A-43, 3.15.6), which is the other half of the design and has its own tests.
        m.remember("the handle writes again")
        m.flush()


# ── the read side: refresh() sees a peer's same-tick write ───────────────────────────────────────────
def test_refresh_sees_a_same_tick_peer_write_on_a_row_store(tmp_path, monkeypatch):
    """A row store's size stays page-aligned, so ANY same-tick write, of any length, left the signature."""
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    p, x = _seeded(tmp_path, False)
    reader, peer = Inspeximus(p), Inspeximus(p)
    assert reader.current("deploy")["object"] == "staging"
    st = os.stat(p)
    peer.remember("the deploy target is production now", key="deploy", object="production")
    peer.flush()
    _same_tick(p, st)
    if (os.stat(p).st_mtime_ns, os.stat(p).st_size) != (st.st_mtime_ns, st.st_size):
        pytest.fail("control: the peer's write moved the stat signature, so the case did not arise")
    assert reader.refresh()["changed"], "refresh() did not see the peer's write"
    assert reader.current("deploy")["object"] == "production"


@pytest.mark.parametrize("encrypted", [False, True], ids=["json", "encrypted"])
def test_refresh_sees_a_same_tick_same_size_peer_write_on_a_whole_file_store(tmp_path, encrypted):
    p, x = _seeded(tmp_path, encrypted)
    reader, peer = _open(p, encrypted), _open(p, encrypted)
    st = os.stat(p)
    peer.credit([x], outcome=1.0)                    # good 1.0 -> 2.0: same length
    peer.flush()
    _same_tick(p, st)
    if (os.stat(p).st_mtime_ns, os.stat(p).st_size) != (st.st_mtime_ns, st.st_size):
        pytest.fail("control: the peer's write moved the stat signature, so the case did not arise")
    assert reader.refresh()["changed"], "refresh() did not see the peer's write"
    assert next(r for r in reader._items if r["id"] == x).get("good") == 2.0


def test_a_row_store_without_a_generation_falls_back_to_the_stat_signature(tmp_path, monkeypatch):
    """A file written before 3.15.4 has no generation: never read as 'unchanged' on that ground alone."""
    import sqlite3
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    p, x = _seeded(tmp_path, False)
    con = sqlite3.connect(p)
    con.execute("DELETE FROM meta WHERE k='generation'")
    con.commit()
    con.close()
    m = Inspeximus(p)
    assert m._file_gen is None
    assert m.refresh()["changed"] is False            # nothing moved: the stat check answers
    Inspeximus(p).remember("a later write", key="later", object="l")   # a 3.15.4 writer adds the counter
    assert m.refresh()["changed"]
