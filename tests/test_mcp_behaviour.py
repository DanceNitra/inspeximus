"""The MCP tools must do what they say, not merely fail to crash.

`tests/test_mcp_surface.py` drives all 55 tools and asserts only "did not raise" and "JSON-serialisable".
That is coverage, and its own docstring says so -- it was demonstrated to pass with six tools replaced by
`lambda: {'lol': 'garbage'}`. A wrapper that returns the wrong record, ignores a parameter, or reports
success while doing nothing is invisible to it.

This file asserts BEHAVIOUR on the tools a user's data actually flows through: the write/read round trip,
correction, erasure, the integrity chain, and the reports whose numbers an auditor reads. Every test here
must fail if its tool is replaced by a plausible-looking stub -- which is checked by mutation, not assumed.

The MCP server is the surface most users touch, and it binds a module-global `_MEM` at import, so the
fixture is module-scoped and restores every environment variable it sets (the sweep file leaked them).
"""
import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("mcp")


@pytest.fixture(scope="module")
def mod():
    """A fresh MCP module on a temp store, with the environment restored afterwards."""
    saved = {k: os.environ.get(k) for k in ("INSPEXIMUS_PATH", "INSPEXIMUS_RECEIPTS")}
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.mkdtemp(), "mcp.json")
    os.environ["INSPEXIMUS_RECEIPTS"] = "1"
    m = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    try:
        yield m
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── the round trip ──────────────────────────────────────────────────────────────────────────────────
def test_remember_then_recall_returns_the_text_that_was_stored(mod):
    """The single most basic contract, and the one a garbage stub breaks first."""
    mod.remember("the staging database runs postgres 14", key="db::staging", object="postgres 14")
    hits = mod.recall("staging database", k=5)
    assert isinstance(hits, list) and hits, "recall must return the record it was just given"
    assert any("postgres 14" in (h.get("text") or "") for h in hits), hits


def test_recall_honours_k(mod):
    """A wrapper that drops the parameter still returns plausible results."""
    for i in range(6):
        mod.remember(f"budget line item number {i} for the quarter")
    assert len(mod.recall("budget line item", k=2)) <= 2
    assert len(mod.recall("budget line item", k=6)) > 2


def test_get_returns_the_record_for_its_id(mod):
    # `remember` returns a DICT ({id, stored, tags, value, mtype}), not a bare id -- and it has no
    # `source` parameter at all, unlike the library call. Read the wrapper's own signature, not the
    # library's: assuming they match is what broke five of these tests on the first run.
    rid = mod.remember("the on-call rotation starts on monday")["id"]
    rec = mod.get(rid)
    assert rec and rec.get("id") == rid and "on-call rotation" in rec.get("text", ""), rec


# ── correction: the product's headline claim ────────────────────────────────────────────────────────
def test_a_correction_supersedes_the_old_value_and_recall_serves_the_new_one(mod):
    mod.remember("the deploy channel is BLUE-9", key="deploy::chan", object="BLUE-9")
    mod.remember("the deploy channel is RED-2", key="deploy::chan", object="RED-2")

    hits = mod.recall("deploy channel", k=5)
    top = (hits[0].get("text") or "") if hits else ""
    assert "RED-2" in top, f"the current value must be served first: {hits[:2]}"

    hist = mod.history("deploy::chan")
    vals = [h.get("object") for h in (hist.get("history") or [])]     # a dict {key, history: [...]}
    assert "BLUE-9" in vals and "RED-2" in vals, f"history must keep both: {hist}"
    current = [h for h in hist["history"] if h.get("status") == "active"]
    assert len(current) == 1 and current[0]["object"] == "RED-2", hist


def test_revert_puts_the_previous_value_back(mod):
    mod.remember("the alert threshold is 10", key="alert::threshold", object="10")
    mod.remember("the alert threshold is 99", key="alert::threshold", object="99")
    res = mod.revert(key="alert::threshold")
    assert res.get("ok") is True, res
    assert res.get("reverted_to_object") == "10", res


# ── erasure: what the compliance story rests on ─────────────────────────────────────────────────────
def test_forget_actually_removes_the_record(mod):
    rid = mod.remember("a record that will be erased in a moment")["id"]
    assert mod.get(rid).get("id") == rid
    mod.forget(ids=[rid])
    # The MCP `get` returns an EMPTY DICT for a missing id, not None -- everything on this surface has to
    # be JSON-serialisable. Asserting `is None` failed against correct behaviour.
    assert not mod.get(rid), "forget() must delete, not merely hide"
    assert not any("will be erased in a moment" in (h.get("text") or "")
                   for h in mod.recall("erased in a moment", k=10))


def test_erasure_certificate_covers_the_erasure_that_happened(mod):
    """The MCP `remember` takes no `source`, so a subject-based DSAR cannot be set up through this surface
    at all -- worth knowing, and the reason this uses id-based erasure instead."""
    rid = mod.remember("bob lives at 9 Elm St")["id"]
    before = set(mod.erasure_certificate().get("erased_memory_ids") or [])
    mod.forget(ids=[rid])

    cert = mod.erasure_certificate()
    erased = set(cert.get("erased_memory_ids") or [])
    assert rid in erased, cert
    assert erased - before == {rid}, "the certificate must grow by exactly the record we erased"
    assert cert.get("count", 0) == len(erased), cert
    assert cert.get("self_check", {}).get("verified") is True, cert


def test_the_mcp_remember_cannot_attach_a_source():
    """Characterisation, not a wish: `forget_subject` resolves a DSAR by canonical SOURCE, and the MCP
    write tool exposes no way to set one -- so every record written through MCP is attributable only to
    its own id. If a `source` parameter is ever added, this fails and should be rewritten."""
    import inspect
    import importlib
    m = importlib.import_module("inspeximus.mcp_server")
    assert "source" not in inspect.signature(m.remember).parameters


# ── integrity: the numbers an auditor reads ─────────────────────────────────────────────────────────
def test_verify_writes_reports_clean_on_an_untampered_store_and_catches_an_edit(mod):
    ok = mod.verify_writes()
    assert ok["ok"] is True, ok                      # {"ok": bool, "problems": [...]}

    rid = mod.remember("Revenue is 100M", mtype="semantic")["id"]
    rec = next(r for r in mod._MEM.items if r["id"] == rid)
    rec["text"] = "Revenue is 900M"
    mod._MEM._save(force=True)
    bad = mod.verify_writes()
    assert bad["ok"] is False, f"an out-of-band edit must be caught through the MCP surface too: {bad}"
    assert bad["problems"], "and it must say what is wrong, not just report not-ok"
    rec["text"] = "Revenue is 100M"                 # restore for the tools that follow
    mod._MEM._save(force=True)


def test_state_digest_moves_when_the_store_moves(mod):
    before = mod.state_digest()
    mod.remember("a brand new fact that changes the state")
    assert mod.state_digest() != before, "the digest must respond to a write"


def test_memory_report_counts_match_the_store(mod):
    rep = mod.memory_report()
    active = len([r for r in mod._MEM.items if r.get("status") == "active"])
    superseded = len([r for r in mod._MEM.items if r.get("status") == "superseded"])
    # Name the keys. My first version searched for the number anywhere in the report's values, which
    # passes whenever ANY field happens to equal it -- a coincidence test, not a contract test.
    assert rep["active"] == active, f"report says {rep['active']}, store has {active}"
    assert rep["superseded"] == superseded, rep
    assert rep["total"] == len(list(mod._MEM.items)), rep


# ── the code-guard pair, sold as the coding-agent fix ────────────────────────────────────────────────
def test_deprecate_symbol_then_check_code_flags_the_resurrected_call(mod):
    mod.deprecate_symbol("old_helper", "new_helper", reason="renamed in the refactor")
    assert mod.symbol_status("old_helper").get("verdict") == "superseded"
    assert mod.symbol_status("new_helper").get("verdict") == "active"

    flags = mod.check_code("result = old_helper(x)\n")
    assert flags and any(f.get("symbol") == "old_helper" for f in flags), flags
    assert not mod.check_code("result = new_helper(x)\n"), "the replacement must not be flagged"


def test_check_code_does_not_flag_a_substring_of_another_identifier(mod):
    """Whole-identifier matching: `old_helper` must not fire on `my_old_helper_v2`."""
    mod.deprecate_symbol("helper", "helper_v2", reason="renamed")
    assert not any(f.get("symbol") == "helper"
                   for f in (mod.check_code("call_the_helper_function(x)\n") or []))


def test_erasure_residue_tool_reports_the_three_kinds(mod, tmp_path):
    """The residue check reachable from an agent. Behaviour, not just "did not raise": an agent that gets
    a clean verdict from a store that still holds the value is worse than no tool."""
    import json as _json
    import sqlite3

    d = tmp_path / "deployment"
    d.mkdir()
    secret = "alice-mcp-probe@example.com"
    (d / "trace.jsonl").write_text(_json.dumps({"pii": secret}), encoding="utf-8")
    con = sqlite3.connect(str(d / "v.sqlite"))
    con.execute("CREATE TABLE t(x TEXT)")
    con.execute("INSERT INTO t VALUES(?)", (secret,))
    con.commit()
    con.close()

    rep = mod.erasure_residue(str(d), [secret])
    kinds = {f["kind"] for f in rep["findings"]}
    assert rep["ok"] is False
    assert {"PLAIN", "LIVE"} <= kinds, rep["findings"]
    assert secret not in _json.dumps(rep), "the tool must not echo the secret back into the transcript"


def test_erasure_residue_tool_is_clean_on_a_clean_directory(mod, tmp_path):
    rep = mod.erasure_residue(str(tmp_path), ["nothing-here-at-all"])
    assert rep["ok"] is True, rep
