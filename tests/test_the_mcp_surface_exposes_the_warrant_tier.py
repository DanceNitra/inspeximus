"""The warrant tier has to be reachable from the surface agents actually use.

Measured 2026-08-08: `with_warrant` existed in the library and appeared NOWHERE in mcp_server.py. The
MCP `recall` never passed it, and `_compact` — the default projection — kept only
{id, text, score, value, tags}, so the tier was not merely off by default, it was uncomputable and
then unrepresentable for every MCP consumer, `full=True` included.

That matters more than where the tier sits in a precedence order. The whole reason the tier exists is
that a low numeric score reads downstream as a weak "yes" and gets acted on; the fix is an explicit
state the caller must branch on. A state the caller cannot obtain does not do that job. We also
dogfood inspeximus through MCP, so the mechanism was absent from our own usage.

This is the same shape as `tests/test_agent_grants.py::test_the_mcp_surface_grants_reads_and_revokes`,
whose docstring already warned: "a wrapper that drops the selector is invisible to the library's own
tests."

The assertions are paired: the tier must APPEAR when asked for, and must NOT appear when it is not —
a projection that always leaks it would pass a presence-only test while breaking every caller that
diffs recall output.
"""
from __future__ import annotations

import importlib
import os
import tempfile

import pytest


def _fresh_module(tmpdir):
    pytest.importorskip("mcp")
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tmpdir, "store.json")
    os.environ["INSPEXIMUS_NO_UPDATE_CHECK"] = "1"
    return importlib.reload(importlib.import_module("inspeximus.mcp_server"))


def test_mcp_recall_can_return_the_warrant_tier():
    with tempfile.TemporaryDirectory() as td:
        mod = _fresh_module(td)
        mod.remember("the orion pipeline retries three times before alerting")

        plain = mod.recall("orion pipeline retries")
        tiered = mod.recall("orion pipeline retries", with_warrant=True)

        assert plain and tiered, "the fixture did not retrieve; the test proves nothing"
        assert all("warrant" not in h for h in plain), (
            "the default MCP projection leaked `warrant` — callers diffing recall output would break")
        assert all("warrant" in h for h in tiered), (
            "with_warrant=True did not reach the caller: either MCP recall drops the argument or "
            "_compact strips the field")
        assert tiered[0]["warrant"] in ("earned", "corroborated", "unwarranted")


def test_the_tier_survives_the_full_projection_too():
    with tempfile.TemporaryDirectory() as td:
        mod = _fresh_module(td)
        mod.remember("the orion pipeline retries three times before alerting")
        hits = mod.recall("orion pipeline retries", with_warrant=True, full=True)
        assert hits and "warrant" in hits[0]


def test_a_lone_self_asserted_memory_reports_unwarranted_through_mcp():
    """Not vacuous: the tier must carry the abstention value, not just exist as a key."""
    with tempfile.TemporaryDirectory() as td:
        mod = _fresh_module(td)
        mod.remember("the vault key rotates every 90 days")
        hits = mod.recall("vault key rotation", with_warrant=True)
        assert hits[0]["warrant"] == "unwarranted", (
            "a single self-asserted memory with no credit and no lineage must read as unwarranted "
            "through MCP, or the surface is reporting confidence it does not have")


def test_asking_for_the_tier_does_not_change_what_is_returned():
    """Additive through the MCP wrapper as well as in the library: same ids, same order."""
    with tempfile.TemporaryDirectory() as td:
        mod = _fresh_module(td)
        for t in ("the orion pipeline retries three times",
                  "orion pipeline alerting threshold is five minutes",
                  "an unrelated note about postgres vacuuming"):
            mod.remember(t)
        for q in ("orion pipeline", "postgres vacuum", "alerting threshold"):
            a = [h["id"] for h in mod.recall(q)]
            b = [h["id"] for h in mod.recall(q, with_warrant=True)]
            assert a == b, f"with_warrant changed ordering/membership through MCP for {q!r}"
