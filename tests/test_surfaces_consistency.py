"""The open items the audits kept reporting about the SURFACES rather than the core.

Four rounds hardened `core.py`. These findings are all one step out from it, and they share a shape: the
library was correct and the thing the user actually touches was not. A guarantee that holds in `Inspeximus`
and fails in the CLI, the MCP tool or an adapter is not a guarantee — it is a footnote.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── one posture across the surfaces that share a store ──────────────────────────────────────────────
@pytest.mark.parametrize("env,expected", [(None, True), ("0", False), ("1", True)])
def test_the_cli_and_the_mcp_server_agree_on_the_echo_guard(env, expected, monkeypatch):
    """They are documented as sharing one store and they disagreed: the MCP turned the guard ON, the CLI left
    the library's legacy default OFF. So one CLI write could resurrect a value the MCP had retired — undoing
    the measured 0.00 -> 1.00 echo-resistance on the very store that advertises it.

    This asserted that the literal `INSPEXIMUS_ECHO_GUARD` appeared in both FILES — a spelling, not a
    behaviour. When the switch moved into the shared opener (`inspeximus/_surface.py`, where the nine
    adapters and the editor hook now read it too), the two surfaces agreed more completely than they ever
    had and this test went red. It now reads the posture off the surfaces themselves, in both directions,
    which is what "agree" was always supposed to mean.
    """
    import importlib
    pytest.importorskip("mcp")
    from inspeximus import cli

    monkeypatch.setenv("INSPEXIMUS_PATH", _path())
    if env is None:
        monkeypatch.delenv("INSPEXIMUS_ECHO_GUARD", raising=False)
    else:
        monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", env)

    mcp = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    assert cli._store(None).echo_guard is expected
    assert mcp._MEM.echo_guard is expected, "both product surfaces must read the same switch"


def test_a_cli_write_does_not_resurrect_a_retired_value():
    p = _path()
    m = Inspeximus(path=p)
    m.echo_guard = True
    m.remember("region is tokyo", key="region", object="tokyo")
    m.remember("region is osaka", key="region", object="osaka")
    m.flush()

    subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p,
                    "remember", "region is tokyo", "--key", "region", "--object", "tokyo"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")

    reopened = Inspeximus(path=p)
    current = [r["object"] for r in reopened.items if r.get("key") == "region" and r["status"] == "active"]
    assert current == ["osaka"], f"the retired value came back through the CLI: {current}"


# ── a record you cannot attribute is a record you cannot erase ──────────────────────────────────────
def test_the_cli_can_write_an_attributable_record():
    """`forget-subject`'s own help said "the source string you wrote with (remember --source)" and
    `remember` had no `--source`, so nothing the CLI wrote could ever be erased by subject."""
    p = _path()
    subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p,
                    "remember", "alice ssn 123", "--source", "alice"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p,
                          "forget-subject", "alice", "--request-id", "DSAR-1"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "erased 1" in out.stdout, out.stdout + out.stderr


@pytest.mark.parametrize("module,func", [
    ("inspeximus.mcp_server", "forget_subject"),
    ("inspeximus.mcp_server", "forget_pii"),
])
def test_the_mcp_erasure_tools_can_record_the_request_they_answer(module, func):
    """Without `request_id` the erasure report keyed everything under `None`, `governance_report()['by_request']`
    was `{None: {...}}` and `erasure_certificate(request_id=...)` could never be scoped — on tools whose
    docstrings sell them as Art.17 evidence."""
    import ast
    import pathlib
    path = pathlib.Path(__file__).parent.parent / (module.replace(".", "/") + ".py")
    node = next(n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(n, ast.FunctionDef) and n.name == func)
    assert "request_id" in [a.arg for a in node.args.args]


def test_every_adapter_write_carries_a_source():
    """A record written without `source=` falls back to `id:<record id>`, so no subject erasure can ever
    reach it. Seven adapters wrote that way — the governance surface the integrations advertise did not
    apply to anything they themselves stored."""
    import ast
    import pathlib
    offenders = []
    for f in sorted((pathlib.Path(__file__).parent.parent / "inspeximus" / "integrations").glob("*.py")):
        # Walk real CALL NODES, not text. Module and class docstrings carry usage examples, and a docstring
        # is not a write — flagging those would train people to ignore this test.
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "remember"):
                continue
            named = {kw.arg for kw in node.keywords}
            if "source" not in named and None not in named:      # None = **kwargs, may carry it
                offenders.append(f"{f.name}:{node.lineno}")
    assert not offenders, ("these adapter writes cannot be erased by subject (no source=): "
                           + ", ".join(offenders))


def test_a_subject_used_by_an_adapter_is_not_a_collision_by_construction():
    """The first version of the fix used the LangGraph namespace joined with '/', and `_canon_source` keeps
    only the host of a path-shaped id — so `users/u1` and `users/u2` both became `users` and EVERY per-user
    erasure was refused as ambiguous. A path-shaped subject is a collision by construction."""
    assert Inspeximus._canon_source("users/u1") == Inspeximus._canon_source("users/u2")      # the trap
    assert Inspeximus._canon_source("lg::users::u1") != Inspeximus._canon_source("lg::users::u2")


# ── a clear that does not clear ─────────────────────────────────────────────────────────────────────
def test_langchain_clear_actually_erases_and_persists():
    """It set `status='deleted'` in memory and stopped: no forget(), no tombstone, no save. `.messages`
    filters by TAG and never looked at status, so the history still returned them — and a reload brought
    every record back active with the content still on disk."""
    pytest.importorskip("langchain_core")
    from langchain_core.messages import HumanMessage

    from inspeximus.integrations.langchain import InspeximusChatMessageHistory
    p = _path()
    h = InspeximusChatMessageHistory("sess-1", path=p)
    h.add_message(HumanMessage(content="card 4111-1111-1111-1111"))
    assert len(h.messages) == 1

    h.clear()
    assert len(h.messages) == 0
    assert "4111-1111" not in open(p, encoding="utf-8").read(), "content survived a user-visible clear"


def test_crewai_reset_actually_erases_and_persists():
    pytest.importorskip("crewai")
    from inspeximus.integrations.crewai import InspeximusStorage
    p = _path()
    s = InspeximusStorage(path=p)
    s.save("secret abc123", {})
    s.reset()
    assert "abc123" not in open(p, encoding="utf-8").read()


def test_a_langgraph_namespace_can_be_erased_as_a_subject():
    """LangGraph's namespace IS the per-user boundary, so it is the subject a DSAR names."""
    pytest.importorskip("langgraph")
    from inspeximus.integrations.langgraph import InspeximusStore
    st = InspeximusStore(path=_path(), receipts=True)
    st.put(("users", "u1"), "pref", {"city": "Rome"})
    st.put(("users", "u2"), "pref", {"city": "Oslo"})
    inner = getattr(st, "store", None) or st._store

    assert inner.forget_subject("lg::users::u1", request_id="DSAR-1", basis="art17")["erased"] >= 1
    assert any("Oslo" in str(r.get("object")) + r["text"] for r in inner.items), "u2 must survive"
