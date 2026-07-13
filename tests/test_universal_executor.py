"""1.2.0: universal-executor gate on spend_irreversible. Opt-in (tool=None) => byte-identical to 1.1.0."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mnemo import Mnemo, is_universal_executor


def test_detects_shell_and_eval_and_sql():
    assert is_universal_executor("Execute")
    assert is_universal_executor("Terminal.Execute")
    assert is_universal_executor({"name": "RunShellCommand", "parameters": [{"name": "command"}]})
    assert is_universal_executor({"name": "eval_code", "parameters": [{"name": "code"}]})
    assert is_universal_executor({"name": "QueryDatabase", "params": ["sql"]})
    assert is_universal_executor("http_request")
    assert is_universal_executor("weird_tool", signature=["shell"])


def test_does_not_flag_dedicated_tools():
    assert not is_universal_executor("SendEmail", signature=["to", "subject", "body"])
    assert not is_universal_executor("ReadEmail", signature=["email_id"])
    assert not is_universal_executor({"name": "DeleteAccount", "parameters": [{"name": "account_id"}]})
    assert not is_universal_executor({"name": "SearchContacts", "parameters": [{"name": "name"}]})


def _store():
    m = Mnemo()
    mid = m.remember("a proven procedure", source={"doc": "ingested"})
    return m, [mid]


def test_uncontained_universal_executor_denied():
    m, ids = _store()
    r = m.spend_irreversible(ids, tool="Terminal.Execute")
    assert r["allowed"] is False
    assert r["universal_executor"] is True
    assert "uncontained" in r["reason"]


def test_contained_universal_executor_falls_through():
    m, ids = _store()
    r = m.spend_irreversible(ids, tool="Terminal.Execute", contained=True, budget=1.0)
    assert r["allowed"] is True          # contained -> normal per-source budget check


def test_dedicated_tool_unaffected():
    m, ids = _store()
    r = m.spend_irreversible(ids, tool="SendEmail")     # not a universal executor -> legacy path
    assert r["allowed"] is True


def test_legacy_path_identical_when_tool_none():
    m, ids = _store()
    a = m.spend_irreversible(ids, amount=0.4, budget=1.0)
    b = m.spend_irreversible(ids, amount=0.4, budget=1.0, tool=None)
    assert a["allowed"] == b["allowed"] is True
