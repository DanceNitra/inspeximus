"""Two MCP tools could not reach the check they were advertising.

Both are the shape this audit kept finding: a surface whose whole purpose is to REFUSE returns a clean
answer about input it structurally never examined — and in both cases the CHECK ALREADY EXISTED one
surface over, in the CLI, wired to a parameter the MCP wrapper simply did not forward.

  * `verify_audit_bundle` never passed `store_items`, so content was never compared. A bundle is
    content-free by design: a clean chain over SUBSTITUTED text verifies PASS. The returned `limits` even
    told the auditor to "pass store_items=" — a parameter that did not exist on this surface. The advice
    was impossible to follow, which is the same defect as no advice at all.
  * `compliance_check` dropped `prior_anchor`, so `not_append_only` (Art. 12/19) could never fire here,
    however the history was rewritten — while the tool docstring listed it among the violations it
    returns. It is the only operator-ADVERSARIAL check of the four.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("mcp")


@pytest.fixture()
def mod(monkeypatch):
    """A fresh MCP module on a temp store with receipts on. Function-scoped: these tests tamper."""
    d = tempfile.mkdtemp()
    monkeypatch.setenv("INSPEXIMUS_PATH", os.path.join(d, "mcp.json"))
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS", "1")
    return importlib.reload(importlib.import_module("inspeximus.mcp_server"))


def _edit_on_disk(path, new_text):
    """The tamper this tool exists to catch: the store FILE edited outside the library.

    Mutating `_MEM.items` in memory and flushing does not persist here, and a test that tampered that way
    would assert nothing while looking thorough — the file still held the honest text.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    recs = data["items"] if isinstance(data, dict) else data
    recs[0]["text"] = new_text
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    with open(path, encoding="utf-8") as fh:
        assert new_text in fh.read(), "the tamper did not reach the file"


# ── verify_audit_bundle: the content check ────────────────────────────────────────────────────────────

def test_a_clean_bundle_over_substituted_text_used_to_pass(mod):
    """The defect, as a control: without the store the chain verifies and content is never examined."""
    mod.remember("the payout wallet is 0xAAA", key="payout::wallet", object="0xAAA")
    mod._MEM.flush()
    bundle = mod.audit_bundle()
    _edit_on_disk(os.environ["INSPEXIMUS_PATH"], "the payout wallet is 0xEVIL")

    blind = mod.verify_audit_bundle(bundle)
    assert blind["ok"] is True, "the chain itself is intact — that is exactly why content had to be checked"
    assert blind["summary"]["content_checked"] is False
    assert any("CONTENT NOT CHECKED" in l for l in blind["limits"])


def test_store_path_catches_the_substituted_text(mod):
    """With the store handed over, the same bundle fails — and says which record."""
    mid = mod.remember("the payout wallet is 0xAAA", key="payout::wallet", object="0xAAA")["id"]
    mod._MEM.flush()
    bundle = mod.audit_bundle()
    _edit_on_disk(os.environ["INSPEXIMUS_PATH"], "the payout wallet is 0xEVIL")

    res = mod.verify_audit_bundle(bundle, store_path=os.environ["INSPEXIMUS_PATH"])
    assert res["summary"]["content_checked"] is True
    assert res["ok"] is False, res
    assert any("no longer match the commitment" in p for p in res["problems"]), res["problems"]
    assert any(mid in p for p in res["problems"]), "the failing record must be named"


def test_store_path_confirms_untouched_content(mod):
    """And it must not cry wolf: an honest store passes with content_checked True."""
    mod.remember("the payout wallet is 0xAAA", key="payout::wallet", object="0xAAA")
    mod._MEM.flush()
    res = mod.verify_audit_bundle(mod.audit_bundle(), store_path=os.environ["INSPEXIMUS_PATH"])
    assert res["ok"] is True, res["problems"]
    assert res["summary"]["content_checked"] is True
    assert any("content binds to the receipts" in c for c in res["checks"])


def test_a_missing_store_path_is_refused_not_silently_downgraded(mod, tmp_path):
    """A mistyped path must not produce a clean verdict — opening a store CREATES it.

    Downgrading to the content-blind answer would be worse than refusing: the caller asked for the
    content check and would be told `ok` by a run that did not do it.
    """
    mod.remember("the payout wallet is 0xAAA")
    mod._MEM.flush()
    bundle = mod.audit_bundle()

    missing = str(tmp_path / "typo.json")
    res = mod.verify_audit_bundle(bundle, store_path=missing)
    assert res["ok"] is False
    assert any("does not exist" in p for p in res["problems"]), res
    assert res["summary"]["content_checked"] is False
    assert not os.path.exists(missing), "the refusal must not have created the store it looked for"


# ── compliance_check: the append-only check ───────────────────────────────────────────────────────────

def test_compliance_check_without_an_anchor_does_not_claim_to_have_checked(mod):
    mod.remember("a first fact")
    res = mod.compliance_check()
    assert "append_only" not in res["checked"]


def test_compliance_check_accepts_a_prior_anchor_and_confirms_an_honest_extension(mod):
    mod.remember("a first fact")
    anchor = mod.anchor()
    mod.remember("a second fact, appended honestly")

    res = mod.compliance_check(prior_anchor=anchor)
    assert "append_only" in res["checked"], "the parameter reached the check"
    assert not [v for v in res["violations"] if v["code"] == "not_append_only"], res["violations"]


def test_compliance_check_fires_not_append_only_when_history_was_rewritten(mod):
    """The violation the docstring promised and the wrapper could never produce."""
    mod.remember("a first fact")
    anchor = mod.anchor()
    mod.remember("a second fact")

    mod._MEM._receipts.pop(0)                       # history rewritten: the pinned prefix is gone
    res = mod.compliance_check(prior_anchor=anchor)

    assert "append_only" in res["checked"]
    codes = [v["code"] for v in res["violations"]]
    assert "not_append_only" in codes, res
    assert res["ok"] is False
