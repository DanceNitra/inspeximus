"""A decision carries one "DECISION:" prefix, whoever wrote the first one.

`remember_decision` prepended "DECISION: " to the text it was given, so a caller that had already written
the prefix stored "DECISION: DECISION: ...". Found in the 3.14.3 install record on a second machine (id
dd8d244dfb); an agent calling `remember_decision` over MCP with its own prefix hits the same path, and so
does the hook's commit capture for a commit subject that starts with "DECISION:". Records already stored
are left as they are.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.claude_code as cc
from inspeximus import Inspeximus


@pytest.fixture
def m(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)
    return Inspeximus(str(tmp_path / "m.json"))


def _rec(m, rid):
    return next(r for r in m.items if r["id"] == rid)


@pytest.mark.parametrize("given", [
    "DECISION: use Postgres for the ledger",
    "decision:use Postgres for the ledger",
    "  Decision :   use Postgres for the ledger",
    "DECISION: DECISION: use Postgres for the ledger",
])
def test_a_prefix_the_caller_wrote_is_not_doubled(m, given):
    r = _rec(m, m.remember_decision(given, because="joins", topic="db"))
    assert r["text"].startswith("DECISION: use Postgres for the ledger"), r["text"]
    assert r["text"].upper().count("DECISION:") == 1, r["text"]
    assert r["object"] == "use Postgres for the ledger", r["object"]


def test_a_decision_without_a_prefix_is_unchanged(m):
    r = _rec(m, m.remember_decision("we made a decision: use Postgres", because="joins", topic="db"))
    assert r["text"].startswith("DECISION: we made a decision: use Postgres"), r["text"]


def test_a_record_already_stored_keeps_its_text(m, tmp_path):
    rid = m.remember("DECISION: DECISION: the old install record", key="decision::install",
                     tags=["decision"], object="DECISION: the old install record")
    m.remember_decision("DECISION: something else", topic="other")
    m.flush()
    again = Inspeximus(str(tmp_path / "m.json"))
    assert _rec(again, rid)["text"] == "DECISION: DECISION: the old install record"


def test_the_hooks_commit_capture_prefixes_once(m, tmp_path):
    proj = str(tmp_path / "proj")
    subprocess.run(["git", "init", "-q", proj], check=True)
    open(os.path.join(proj, "a.txt"), "w").close()
    g = ["git", "-C", proj, "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(g + ["add", "a.txt"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "DECISION: adopt the row store"], check=True)
    if not cc._capture_commit(m, 'git commit -m "DECISION: adopt the row store"', proj, "s"):
        pytest.fail("control: the hook captured no commit")
    r = next(r for r in m.items if (r.get("key") or "").startswith("commit::"))
    assert r["text"].startswith("DECISION: adopt the row store"), r["text"]
    assert r["text"].upper().count("DECISION:") == 1, r["text"]
