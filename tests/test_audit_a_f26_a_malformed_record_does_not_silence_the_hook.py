"""AUDIT-A F-26 (3.16.4): one record of the wrong shape must not switch the prompt hook off.

A record whose `meta` was a string, a list or a number made every recall raise `AttributeError`, and the prompt hook
printed "[inspeximus] hook UserPromptSubmit failed" and no memory. Measured on d55af8e6: the same for `text`, `tags`,
`links`, `value` and `key` of the wrong type. A store a repository ships could switch the user's memory off for that
project. `Inspeximus._normalise_loaded` now gives the field a safe value, keeps the original under meta["malformed"],
and quarantines the record, so recall withholds it until release_quarantine and serves the rest."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import claude_code as cc  # noqa: E402

CASES = [("meta", "oops"), ("meta", [1, 2]), ("meta", 7), ("text", 5), ("tags", 5), ("links", 5),
         ("value", "z"), ("key", 5)]


@pytest.fixture
def project(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    proj = tmp_path / "repo"
    (proj / ".git").mkdir(parents=True)
    return proj


def _break(proj, field, value):
    m = cc._store(str(proj))
    for i in range(6):
        m.remember("a note about the release, number %d" % i, key="n%d" % i)
    m.flush()
    r = m.items[0]
    r[field] = value
    m._touched.add(r["id"])
    m.flush()
    return r["id"]


def _hook(proj):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = ROOT
    ev = {"hook_event_name": "UserPromptSubmit", "prompt": "what did we note about the release", "session_id": "t",
          "cwd": str(proj)}
    return subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev), cwd=str(proj.parent),
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


@pytest.mark.parametrize("field,value", CASES)
def test_the_prompt_hook_still_answers(project, field, value):
    _break(project, field, value)
    r = _hook(project)
    assert r.returncode == 0 and "failed" not in r.stderr and r.stdout.strip(), (field, value, r.stderr[-200:])
    assert "release" in r.stdout


def test_the_record_is_withheld_and_its_original_value_kept(project):
    bad = _break(project, "meta", "oops")
    m = cc._store(str(project))
    rec = next(r for r in m.items if r["id"] == bad)
    assert rec["meta"]["malformed"] == {"meta": "oops"}
    assert rec["meta"]["quarantined"]["reason"] == "malformed_record"
    hits = [h["id"] for h in m.recall("note about the release", k=20)]
    assert bad not in hits and len(hits) == 5, hits


def test_control_a_well_formed_record_is_not_touched(project):
    m = cc._store(str(project))
    m.remember("a plain note", key="plain", tags=["t"])
    m.flush()
    rec = next(r for r in cc._store(str(project)).items if r.get("key") == "plain")
    assert "malformed" not in rec["meta"] and "quarantined" not in rec["meta"]
