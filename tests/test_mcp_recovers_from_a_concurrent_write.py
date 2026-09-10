"""A write refused for a concurrent change must reload once and land, and must still fail twice.

WHAT THIS PINS. Measured on our own store on 2026-09-08: three inspeximus-mcp processes ran against
one file, a `remember_decision` was refused four times in a row with `StoreChangedOnDisk`, and the
store's last write was the previous evening. The guard was right to refuse. The problem was that the
client could not get back: the exception's own message says "Call reload() to merge the two and
retry", `reload` is not an MCP tool, `StoreChangedOnDisk` appeared nowhere in mcp_server.py, and the
handle is a module-level singleton, so one stale handle locked every write tool until the process
restarted.

BOTH DIRECTIONS ARE TESTED, because fixing only the first would let the retry become a way to spin or
to overwrite:

  a concurrent write is survived     the second writer's record lands, and the first writer's record
                                     is still there afterwards, which is what makes reload a merge
                                     rather than a clobber
  a second failure still propagates  a store that refuses twice raises, so a genuinely contended
                                     store reports instead of looping
  exactly one retry                  the underlying call is attempted twice, never three times

The wrap is exercised directly rather than through FastMCP, because what is under test is the
recovery, not the tool transport.
"""
import os
import tempfile

import pytest

# EVERY test here reaches `inspeximus.mcp_server`, which needs the MCP SDK, and CI installs it in
# only one job. Without this the whole file errors at COLLECTION, and a collection error is not a
# failed assertion, so the three tests would go missing rather than red. The guard belongs to the
# dependency it guards, so it sits at module level here where all three tests need it, and never in
# a file whose other tests need nothing optional.
pytest.importorskip("mcp")

from inspeximus.core import Inspeximus, StoreChangedOnDisk


def _wrap(store):
    """The same wrap mcp_server applies to its singleton, imported from it so the test cannot drift."""
    from inspeximus.mcp_server import _recover_from_concurrent_writes
    return _recover_from_concurrent_writes(store)


def test_a_write_survives_another_process_writing_first():
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    a = Inspeximus(path=path)
    a.remember("the first writer's record", mtype="semantic")

    b = Inspeximus(path=path)          # loads now
    a.remember("what the other process wrote in between", mtype="semantic")

    # THE UNWRAPPED CALL IS NOT ASSERTED TO RAISE, and the first version of this test did assert it.
    # On a ROW store the library merges by id and never reaches the refusal, which is the better fix
    # and landed in b411754 on 2026-09-07. The refusal is reachable on the JSON path and when the
    # merge itself fails, which is what this wrap still covers. What must hold either way is the
    # outcome below: the write lands and the other writer's record survives.
    _wrap(b)
    mid = b.remember("the wrapped write, which must land", mtype="semantic")
    assert mid

    fresh = Inspeximus(path=path)
    texts = [r["text"] for r in fresh.items]
    assert "the wrapped write, which must land" in texts
    # THE MERGE, not a clobber: the other process's record must still be there.
    assert "what the other process wrote in between" in texts
    assert "the first writer's record" in texts


def test_a_second_refusal_still_raises():
    """Otherwise the retry is a way to spin on a contended store instead of reporting."""
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    m = Inspeximus(path=path)
    m.remember("seed", mtype="semantic")
    _wrap(m)

    calls = {"n": 0}
    original = Inspeximus.remember

    def always_refuses(self, *a, **k):
        calls["n"] += 1
        raise StoreChangedOnDisk("forced")

    Inspeximus.remember = always_refuses
    try:
        # Rebuild the wrap over the patched method so the wrapper calls the refusing one.
        fresh = Inspeximus(path=path)
        _wrap(fresh)
        with pytest.raises(StoreChangedOnDisk):
            fresh.remember("never lands", mtype="semantic")
    finally:
        Inspeximus.remember = original
    assert calls["n"] == 2, "exactly one retry: attempted %d times" % calls["n"]


def test_the_wrap_is_idempotent():
    """The server may wrap once at import; wrapping twice must not double the retries."""
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    m = Inspeximus(path=path)
    _wrap(m)
    first = m.remember
    _wrap(m)
    assert m.remember is first
