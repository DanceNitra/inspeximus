"""AUDIT-B 3.16.3: the hook skips a decision store that is too big to read on every prompt, and says so.

`INSPEXIMUS_DECISION_STORE` is opened and queried by every UserPromptSubmit. Measured 2026-10-04: a 4 MB store of
619 decisions costs +0.59 s per prompt, and the 53 MB MCP chain store, configured there by mistake, costs 5.29 s with
nothing in the output to say why. Over DECISION_STORE_MAX_MB the store is skipped and one line goes to stderr.
Also here: `--stamp-guards --store PATH` stamps the decision store the hook cannot save.
"""
import io
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DECISION = "we decided the zebra protocol uses the green handshake"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path_factory.mktemp("key-home")))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")


def _setup(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".inspeximus").mkdir()
    d = tmp_path / "decisions"
    d.mkdir()
    dpath = str(d / "mcp_memory.json")
    m = Inspeximus(dpath)
    m.remember_decision(DECISION, because="a handshake is needed", topic="zebra-protocol")
    m.flush()
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", dpath)
    return str(proj), dpath


def _hook(proj, capsys):
    ev = {"hook_event_name": "UserPromptSubmit", "prompt": "what is the zebra protocol handshake",
          "cwd": proj.replace("\\", "/"), "session_id": "t"}
    cc.recall(ev)
    out = capsys.readouterr()
    return out.out, out.err


def test_a_small_decision_store_is_read_and_says_nothing(tmp_path, monkeypatch, capsys):
    proj, dpath = _setup(tmp_path, monkeypatch)
    out, err = _hook(proj, capsys)
    assert "green handshake" in out, "control: the decision is injected"
    assert err == ""


def test_a_decision_store_over_the_limit_is_skipped_with_one_line_on_stderr(tmp_path, monkeypatch, capsys):
    proj, dpath = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE_MAX_MB", "0.0001")
    out, err = _hook(proj, capsys)
    assert "green handshake" not in out
    assert err.count("INSPEXIMUS_DECISION_STORE") == 2 and err.count(chr(10)) == 1, err
    assert "INSPEXIMUS_DECISION_STORE_MAX_MB" in err and "MB" in err


def test_a_limit_of_zero_turns_the_check_off(tmp_path, monkeypatch, capsys):
    proj, dpath = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE_MAX_MB", "0")
    out, err = _hook(proj, capsys)
    assert "green handshake" in out and err == ""


@pytest.mark.parametrize("raw", ["abc", "", "  ", "1e"])
def test_an_invalid_limit_falls_back_to_the_default(tmp_path, monkeypatch, capsys, raw):
    proj, dpath = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE_MAX_MB", raw)
    out, err = _hook(proj, capsys)
    assert "green handshake" in out and err == ""


def test_a_missing_decision_store_does_not_raise(tmp_path, monkeypatch, capsys):
    proj, dpath = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", str(tmp_path / "nope" / "x.json"))
    _hook(proj, capsys)                                           # fail-open: no exception is the assertion


def test_stamp_guards_store_stamps_the_named_store(tmp_path, monkeypatch):
    proj, dpath = _setup(tmp_path, monkeypatch)
    m = Inspeximus(dpath)
    for r in m._items:
        (r.get("meta") or {}).pop("read_guards", None)
        m._touched.add(r["id"])
    m._save(force=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_") and k != "PYTHONPATH"}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ["INSPEXIMUS_KEY_HOME"], INSPEXIMUS_NO_UPDATE_CHECK="1",
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), APPDATA=str(tmp_path))
    run = lambda *a: subprocess.run([sys.executable, "-m", "inspeximus.claude_code", "--stamp-guards", "--store",
                                     dpath, *a], capture_output=True, text=True, encoding="utf-8", cwd=proj,
                                    env=env, timeout=120)
    dry = run()
    assert dry.returncode == 0 and '"to_stamp": 1' in dry.stdout, dry.stdout + dry.stderr
    assert Inspeximus(dpath).stamp_read_guards(dry_run=True)["to_stamp"] == 1, "a dry run changed the store"
    done = run("--apply")
    assert '"stamped": 1' in done.stdout, done.stdout + done.stderr
    assert Inspeximus(dpath).stamp_read_guards(dry_run=True)["to_stamp"] == 0
