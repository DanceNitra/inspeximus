"""INSPEXIMUS_OBSERVE_RECALL has to reach the STORE, not just a module variable.

2.2.0 shipped `observe_recall` in the library and this server could not switch it on: `_MEM = open_store(...)`
passed `receipts=` and nothing else. The capability existed and had no consumer for a full release.

That mattered here more than anywhere else. This server holds ONE module-level store for the whole process,
so `recall` followed by `remember`/`remember_decision` is the same agent on the same store, causally linked
— it is the surface where the recall->write flow is real, which is the entire reason the field exists.

THE TEST THAT WOULD HAVE CAUGHT IT is not `assert m._OBSERVE_RECALL is True`. A module flag that is read and
then dropped on the floor passes that and changes nothing. Every check below goes through `_MEM` and asserts
on a written record.
"""
import importlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pytest
    pytest.importorskip("mcp.server.fastmcp")
except ImportError:                         # standalone, without pytest
    try:
        import mcp.server.fastmcp  # noqa: F401
    except Exception:
        print("SKIP: MCP SDK not installed"); sys.exit(0)


def _fresh_server(observe):
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.mkdtemp(), "m.json")
    if observe is None:
        os.environ.pop("INSPEXIMUS_OBSERVE_RECALL", None)
    else:
        os.environ["INSPEXIMUS_OBSERVE_RECALL"] = observe
    import inspeximus.mcp_server as m
    return importlib.reload(m)


def _clean():
    os.environ.pop("INSPEXIMUS_OBSERVE_RECALL", None)
    os.environ.pop("INSPEXIMUS_PATH", None)


def test_the_flag_reaches_the_store_and_a_write_after_a_recall_carries_the_window():
    m = _fresh_server("1")
    try:
        assert m._OBSERVE_RECALL is True
        assert m._MEM.observe_recall is True, "the env var was read but never passed to open_store()"

        a = m._MEM.remember("the staging database is db-7.internal")
        m._MEM.remember("db-7 runs postgres 16")
        served = [h["id"] for h in m._MEM.recall("staging database", k=4)]
        assert a in served, "fixture no longer reproduces: the recall served nothing relevant"

        wid = m._MEM.remember("summary: staging is db-7 on postgres 16")
        rec = next(r for r in m._MEM.items if r["id"] == wid)
        assert rec.get("recall_window"), "the write carried no observation"
        assert rec["recall_window"]["ids"] == served
        assert rec["recall_window"]["w"] == 0
        # ...and it stayed an observation: no lineage was claimed on its behalf.
        assert not rec.get("derived_from") and not rec.get("taint")
    finally:
        _clean()


def test_the_recall_TOOL_and_the_remember_TOOL_pair_up_over_the_mcp_surface():
    """The library test covers store.recall -> store.remember. This covers the MCP tools a client calls,
    which is the path that actually carries the traffic."""
    m = _fresh_server("1")
    try:
        m.remember("the billing service retries three times")
        m.remember("retries use exponential backoff")
        hits = m.recall("billing retries", k=4)
        assert hits, "fixture no longer reproduces: the recall tool returned nothing"

        # the tool returns a dict, not a bare id -- read the field rather than assuming the shape
        out = m.remember_decision("we cap billing retries at three",
                                  because="beyond three the backoff exceeds the request timeout",
                                  topic="billing::retries")
        rid = out["id"] if isinstance(out, dict) else out
        rec = next(r for r in m._MEM.items if r["id"] == rid)
        assert rec.get("recall_window"), (
            "remember_decision is the main write path for an agent and carried no window")
        assert rec["recall_window"]["ids"] == [h["id"] for h in hits]
    finally:
        _clean()


def test_default_is_off_and_writes_are_unstamped():
    m = _fresh_server(None)
    try:
        assert m._OBSERVE_RECALL is False
        assert m._MEM.observe_recall is False
        m._MEM.remember("a fact")
        m._MEM.recall("fact", k=4)
        wid = m._MEM.remember("a later write")
        assert "recall_window" not in next(r for r in m._MEM.items if r["id"] == wid)
    finally:
        _clean()


def test_the_env_var_accepts_the_same_spellings_as_the_other_switches():
    """`_RECEIPTS` accepts 1/true/yes/on; a switch that silently ignores "true" is a support ticket."""
    for on in ("1", "true", "TRUE", "yes", "on"):
        m = _fresh_server(on)
        try:
            assert m._MEM.observe_recall is True, f"{on!r} did not enable it"
        finally:
            _clean()
    for off in ("0", "false", "no", "", "off"):
        m = _fresh_server(off)
        try:
            assert m._MEM.observe_recall is False, f"{off!r} did not leave it off"
        finally:
            _clean()
