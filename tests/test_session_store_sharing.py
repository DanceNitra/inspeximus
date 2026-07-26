"""Two sessions on one store file must share one handle.

Caught by CI, not by the suite: `session_audit.py` compares `InspeximusSession` against the SDK's own
`SQLiteSession` and reported "NOT a drop-in -- 4 mismatches". Two `InspeximusSession(path=same_file)` built
two independent `Inspeximus` handles, and the single-writer guard then raised `StoreChangedOnDisk` on the
second one's first write.

The guard is right in general — two handles on one file each believe they hold the whole store, so the last
save erases the other's records — and wrong here: the reference keeps one connection per DB file, and this
class's own docstring promises "one store, many sessions". The fix shares the handle per resolved path
within the process; separate processes still get the guard.

The audit script needs the optional `agents` SDK, so it only runs in CI. These do not.
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
from inspeximus.integrations.openai_agents import InspeximusSession


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _msg(text):
    return {"role": "user", "content": text}


def test_two_sessions_on_one_path_share_a_handle():
    p = _path()
    assert InspeximusSession(session_id="s1", path=p).store is InspeximusSession(session_id="s2", path=p).store


def test_a_second_session_can_write_without_a_conflict():
    """The actual failure: the second session's first `add_items` raised."""
    p = _path()
    a, b = InspeximusSession(session_id="s1", path=p), InspeximusSession(session_id="s2", path=p)
    asyncio.run(a.add_items([_msg("session one secret ALPHA")]))
    asyncio.run(b.add_items([_msg("session two secret BETA")]))       # used to raise StoreChangedOnDisk

    mine = [i["content"] for i in asyncio.run(a.get_items())]
    theirs = [i["content"] for i in asyncio.run(b.get_items())]
    assert any("ALPHA" in c for c in mine) and not any("BETA" in c for c in mine)
    assert any("BETA" in c for c in theirs) and not any("ALPHA" in c for c in theirs)


def test_sharing_a_handle_does_not_merge_the_sessions():
    """Isolation is by `session_id`, and it must not weaken now that the store object is shared."""
    p = _path()
    a, b = InspeximusSession(session_id="s1", path=p), InspeximusSession(session_id="s2", path=p)
    asyncio.run(a.add_items([_msg("one")]))
    asyncio.run(b.add_items([_msg("two")]))
    asyncio.run(b.clear_session())
    assert [i["content"] for i in asyncio.run(a.get_items())] == ["one"], "clearing s2 must not touch s1"


def test_different_paths_do_not_share():
    assert InspeximusSession(session_id="s", path=_path()).store is not \
        InspeximusSession(session_id="s", path=_path()).store


def test_an_explicit_store_is_still_honoured():
    """The documented way to share stays the documented way."""
    st = Inspeximus(path=_path())
    assert InspeximusSession(session_id="s1", store=st).store is st


def test_the_single_writer_guard_is_not_disabled():
    """Sharing the handle must not be mistaken for switching the guard off — two independent handles on one
    file is still the case where a save erases the other's records."""
    p = _path()
    a, ops = Inspeximus(path=p), 0
    a.remember("x")
    a.flush()
    b = Inspeximus(path=p)
    # Assert the PROPERTY (two independent handles cannot both keep writing), not the exact call that
    # raises. My first version pinned it to a's second flush and failed — the guard fired one step earlier.
    try:
        for handle, text in ((b, "y"), (a, "z"), (b, "w")):
            handle.remember(text)
            handle.flush()
            ops += 1
        raise AssertionError("the single-writer guard stopped firing")
    except StoreChangedOnDisk:
        assert ops < 3


def test_the_handle_is_released_when_the_last_session_goes():
    """A process-global cache that never lets go is a leak; the registry is weak-valued."""
    import gc

    import inspeximus.integrations.openai_agents as oa
    p = _path()
    s = InspeximusSession(session_id="s1", path=p)
    key = str(__import__("pathlib").Path(os.path.expanduser(p)).resolve())
    assert key in oa._OPEN_STORES
    del s
    gc.collect()
    assert key not in oa._OPEN_STORES
